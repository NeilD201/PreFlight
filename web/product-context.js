'use strict';

// A local guided conversation: no provider request per message and no browser
// storage of product details. The server validates answers before saving them.
const productInterview = {drafts:new Map(), active:false};

function maybeAskProductQuestions(){
  const job=app.snapshot.job;
  if(app.busy || app.pending || app.adding || productInterview.active || !app.project || app.view!=='workspace')return;
  if(typeof document!=='undefined' && document.querySelector('dialog[open]'))return;
  const intake=app.project.review_inputs?.needs_product_profile || app.project.ai_review?.status==='needs_context' || (job?.needs_product_context && job.status==='completed');
  const pending=app.project.review_inputs?.count>0;
  if(!intake && !pending)return;
  const prompt=app.snapshot.report_id;
  if(productInterview.promptedReport===prompt)return;
  productInterview.promptedReport=prompt;
  // Let refresh finish before the answer callback can start another job.
  setTimeout(()=>{
    if(prompt!==app.snapshot.report_id)return;
    return (intake?editProductContext(false):editReviewInputs()).catch(showError);
  },0);
  return true;
}

async function confirmAIRun(){
  // A configured key is never permission to spend: the user explicitly
  // chooses between a free local reconsideration and a paid investigation.
  if(typeof productInterview.confirmAI==='function')return !!(await productInterview.confirmAI());
  if(typeof document==='undefined')return false;
  return new Promise(resolve=>{
    const dialog=node('dialog','product-interview'),closed={done:false};
    function finish(value){if(closed.done)return;closed.done=true;dialog.close();dialog.remove();resolve(value);}
    dialog.addEventListener('cancel',event=>{event.preventDefault();finish(false);});
    dialog.addEventListener('close',()=>finish(false));
    const heading=node('div','dialog-heading'),title=node('h2','','Answers saved');
    title.id='product-interview-title';heading.append(title);dialog.append(heading);
    dialog.append(node('p','','Your answers are saved with this project and local findings were reconsidered at no cost.'));
    dialog.append(node('p','field-help','A Vector investigation uses Meta API credits and re-examines the board with your answers.'));
    const footer=node('div','dialog-actions');
    const run=node('button','primary','Run Vector investigation');run.type='button';
    run.addEventListener('click',()=>finish(true));
    const save=node('button','secondary','Save only');save.type='button';
    save.addEventListener('click',()=>finish(false));
    footer.append(save,run);dialog.append(footer);
    document.body.append(dialog);dialog.showModal();run.focus();
  });
}

async function editProductContext(showSummary=true){
  if(app.busy || productInterview.active)return;
  const report=app.snapshot.report_id;
  const revision=app.project.workspace.design_context.revision;
  const answers=await interviewProduct(app.project.workspace.design_context.product_context || {},
    'report:'+report,'Use answers & review',showSummary,report,revision,'check');
  if(answers===null)return;
  if(report!==app.snapshot.report_id){
    showError(new Error('The review changed while you were answering. Open product details again.'));return;
  }
  const wantsAI=app.project.ai_review?.status==='needs_context'?app.project.ai_review.resume_ai===true:app.config.key_configured;
  if(app.project.review_inputs?.count){
    await refresh();
    if(!await interviewReviewInputs())return;
  }
  await rethink({datasheets:false,ai:wantsAI?await confirmAIRun():false});
}

async function interviewProduct(initial,scope,finishLabel,showSummary=false,reportId,contextRevision,flow=null){
  if(productInterview.active)return null;
  productInterview.active=true;
  let answers={...initial},state,index=0,closed=false,busy=false,returnToSummary=false,bulk=false;
  // One free-text project description replaces the mandatory per-question
  // sequence as the first step: it is stored as the shared device
  // description, reaches the Vector context, and can be edited later from
  // the summary screen or the design view. `undefined` means not loaded yet.
  let description,descriptionAsked=false;
  const dialog=node('dialog','product-interview');
  dialog.setAttribute('aria-labelledby','product-interview-title');
  document.body.append(dialog);
  let resolveResult;
  const result=new Promise(resolve=>{resolveResult=resolve;});
  function finish(value){
    if(closed)return;
    closed=true;productInterview.active=false;
    productInterview.drafts.set(scope,{answers:{...answers},confirmed:value!==null});
    dialog.close();dialog.remove();resolveResult(value);
  }
  dialog.addEventListener('cancel',event=>{event.preventDefault();finish(null);});
  dialog.addEventListener('close',()=>finish(null));
  async function load(save=false,updates={}){
    state=await api(save?'/api/product-answers':'/api/product-questions',
      {report_id:reportId,...(save?{answers:updates}:{}),context_revision:contextRevision,...(flow?{flow}:{})});
    contextRevision=state.context_revision;
    answers=state.answers;
    if(app.snapshot.report_id===reportId){
      const context=app.project.workspace.design_context;
      context.product_context={...answers};context.revision=contextRevision;
    }
    productInterview.drafts.set(scope,{answers:{...answers},confirmed:false});
  }
  function button(text,style,action){
    const b=node('button',style,text);b.type='button';b.addEventListener('click',action);return b;
  }
  function displayAnswer(q,value){
    const i=(q.choices||[]).indexOf(value);
    if(i>=0 && q.choice_labels?.[i])return q.choice_labels[i];
    return value || 'Not answered';
  }
  async function drawDescription(){
    // The opening step: one large box for the whole project story. Saving
    // stores the shared device description (no API credits, no analysis
    // yet); the normal flow continues afterwards so genuinely missing
    // hardware details can still be asked about one at a time.
    if(closed)return;
    descriptionAsked=true;
    try{
      const ctx=await api('/api/project-context',{report_id:reportId});
      description=(ctx&&ctx.description)||'';
      if(ctx&&ctx.context_revision)contextRevision=ctx.context_revision;
    }catch(e){description='';}
    if(closed)return;
    dialog.replaceChildren();
    const heading=node('div','dialog-heading'),title=node('h2','','Tell PreFlight about your project');title.id='product-interview-title';
    heading.append(title,button('×','icon-button',()=>finish(null)));
    heading.lastChild.setAttribute('aria-label','Close product questions');dialog.append(heading);
    dialog.append(node('p','field-help','Describe what you\u2019re building, how the circuit is supposed to work, important components, voltages, interfaces, design requirements, or anything else PreFlight should know. Write as much as you like — this is saved with the project and passed to the engineering investigation.'));
    const error=node('p','error-message');error.setAttribute('role','alert');
    const area=node('textarea');area.id='project-description';area.rows=10;area.maxLength=6000;area.value=description||'';
    area.placeholder='Example: “Battery-powered sensor node with an ESP32-S3, BME280 over I2C at 3.3 V, USB-C for charging only…”';
    area.setAttribute('aria-label','Project description');dialog.append(area,error);
    const footer=node('div','dialog-actions');
    const save=node('button','primary','Save & analyse');save.type='button';
    footer.append(button('Pause for now','secondary',()=>finish(null)),
      button('Answer questions instead','secondary',()=>{description='';draw();}),save);
    dialog.append(footer);area.focus();
    save.addEventListener('click',async()=>{
      if(busy)return;busy=true;save.disabled=true;error.textContent='';
      const text=area.value.trim();
      if(!text){error.textContent='Describe the project first, or choose “Answer questions instead”.';busy=false;save.disabled=false;return;}
      try{
        const saved=await api('/api/project-context',{report_id:reportId,description:text,context_revision:contextRevision});
        contextRevision=saved.context_revision;description=text;
        if(app.snapshot.report_id===reportId){
          const context=app.project.workspace.design_context;
          context.description=text;context.revision=contextRevision;
        }
        if(closed)return;
        draw();
      }catch(e){if(!closed)error.textContent=e.message;}
      finally{busy=false;if(!closed)save.disabled=false;}
    });
  }
  function draw(summary=false){
    if(closed)return;
    if(flow && !descriptionAsked && description===undefined){drawDescription();return;}
    dialog.replaceChildren();
    const introduction=!summary && state.questions[index]?.id==='experience';
    const heading=node('div','dialog-heading'),title=node('h2','',summary?'Answers saved':introduction?'Let’s start with you':'A detail is needed to continue');title.id='product-interview-title';
    heading.append(title,button('×','icon-button',()=>finish(null)));
    heading.lastChild.setAttribute('aria-label','Close product questions');dialog.append(heading);
    dialog.append(node('p','field-help',summary?
      `${state.answered_questions?.length || 0} answers saved to this project. Edit any answer below. Saving uses no API credits.`:
      introduction?'Your answer helps us explain issues at the right level. It is saved with this project, and you can change it later.':
      'Each answer is saved to this project when you press Continue, including “Not sure yet”. You can close and return later. Saving answers uses no API credits.'));
    if(state.deferred_count)dialog.append(node('p','field-help','More questions will appear as you answer. You do not need to find them elsewhere.'));
    const error=node('p','error-message');error.setAttribute('role','alert');
    if(!state.questions.length){
      dialog.append(node('p','','No unanswered questions were identified for the circuit patterns this check recognizes. This does not verify all operating requirements.'));
      renderQuestionAnswers(dialog,revision=>{contextRevision=revision;});
      dialog.append(button('Continue review','primary',()=>finish({...answers})),button('Pause for now','secondary',()=>finish(null)));return;
    }
    if(summary){
      dialog.append(node('h3','','Here is what the review will use'));
      const list=node('div','product-answer-list');
      const saved=state.answered_questions || state.questions.filter(q=>answers[q.id]);
      saved.forEach(q=>{
        const item=node('div','product-answer');
        item.append(node('strong','',q.question),node('p','',displayAnswer(q,answers[q.id])),
          button('Edit','text-button',()=>{index=state.questions.findIndex(item=>item.id===q.id);if(index<0){state.questions.push(q);index=state.questions.length-1;}returnToSummary=true;draw();}));list.append(item);
      });
      dialog.append(list,node('p','field-help','Answers are saved with this project. Later reviews, chat and repair planning use them as your stated assumptions.'));
      renderQuestionAnswers(dialog,revision=>{contextRevision=revision;});
      const history=app.project?.workspace?.conversation_history || [];
      if(history.length){
        const archive=node('details');archive.append(node('summary','','Saved conversations'));
        archive.append(node('p','field-help','Most recent 100 messages shown. Full history is kept in the project database; recent messages are included in Vector context. Earlier assistant replies are not treated as verified requirements.'));
        for(const entry of history){
          archive.append(node('small','',`${entry.channel} · ${new Date(entry.at).toLocaleString()}`),node('p','',entry.question));
          if(entry.answer)archive.append(node('p','field-help',entry.answer));
        }
        dialog.append(archive);
      }
      const footer=node('div','dialog-actions');
      if(state.unanswered.length)footer.append(button('Answer remaining questions','secondary',()=>{index=state.questions.findIndex(q=>!answers[q.id]);draw();}));
      footer.append(button('Close — answers saved','secondary',()=>finish(null)),
        button(finishLabel,'primary',()=>finish({...answers})));
      dialog.append(footer);return;
    }
    if(bulk){drawBulk();return;}
    const q=state.questions[index];
    dialog.append(node('p','field-help',q.id==='experience'?'A little about you':`Question ${index+1} of ${state.questions.length} · ${q.reference}`));
    if(index>0){
      const previous=state.questions[index-1];
      const bubble=node('div','product-reply');bubble.append(node('small','','Your previous answer'),node('p','',displayAnswer(previous,answers[previous.id])));
      dialog.append(bubble);
    }
    const facts=node('div','product-reply');facts.append(node('small','',q.reference==='Your product'?'About this question':'What your files already show'),node('p','',q.known));
    if(q.evidence?.length){const detail=node('details');detail.append(node('summary','','Saved connections'));for(const line of q.evidence)detail.append(node('p','',line));facts.append(detail);}
    if(!introduction)dialog.append(facts);
    const label=node('label','product-question',q.question);label.htmlFor='product-answer';
    const hint=node('p','field-help',q.hint);hint.id='product-answer-hint';
    const input=node('textarea');input.id='product-answer';input.rows=4;input.maxLength=1200;input.value=answers[q.id] || '';
    input.setAttribute('aria-describedby',hint.id);input.placeholder='Write in your own words…';
    const choices=node('div','scope-answer-choices');
    if(q.choices?.length){
      for(const [choiceIndex,choice] of q.choices.entries())choices.append(button(q.choice_labels?.[choiceIndex] || choice,'secondary',()=>{
        input.value=choice;answers[q.id]=choice;
        productInterview.drafts.set(scope,{answers:{...answers},confirmed:false});
      }));
    }
    input.addEventListener('input',()=>{answers[q.id]=input.value;productInterview.drafts.set(scope,{answers:{...answers},confirmed:false});});
    const form=node('form'),footer=node('div','dialog-actions');
    async function next(unknown=false,skipRest=false){
      if(busy)return;busy=true;
      answers[q.id]=unknown?'Not sure yet':input.value.trim();
      if(!answers[q.id] && !skipRest){error.textContent='Add an answer, choose “Not sure yet”, or skip all remaining questions.';busy=false;return;}
      for(const b of form.querySelectorAll('button'))b.disabled=true;
      try{
        await load(true,{[q.id]:answers[q.id]});
        if(skipRest)await load(true,Object.fromEntries(state.unanswered.map(key=>[key,'Not sure yet'])));
        if(closed)return;
        // The server may insert follow-ups or move unanswered questions ahead
        // of completed ones. Follow IDs/answer state, never the old index.
        index=state.questions.findIndex(item=>!answers[item.id]);
        if(skipRest || index<0 || returnToSummary){returnToSummary=false;draw(true);}else draw();
      }catch(e){
        if(closed)return;
        error.textContent=e.message;
        for(const b of form.querySelectorAll('button'))b.disabled=false;
        // A re-analysis since this dialog opened leaves it holding a stale
        // report. Offer inline recovery instead of a dead-end 409 loop.
        if(/open the current project/i.test(e.message || '') && !form.querySelector('[data-reload-review]')){
          const reload=button('Reload latest review','secondary',async ()=>{
            finish(null);
            try{await refresh();await editProductContext(false);}catch(err){showError(err);}
          });
          reload.setAttribute('data-reload-review','1');footer.append(reload);
        }
      }
      finally{busy=false;}
    }
    if(index>0)footer.append(button('Back','secondary',()=>{index--;draw();}));
    footer.append(button('Not sure yet','secondary',()=>next(true)));
    footer.append(button('Skip all','secondary',()=>next(false,true)));
    footer.append(button('Enter all details','secondary',()=>{bulk=true;draw();}));
    const nextButton=node('button','primary','Continue');nextButton.type='submit';footer.append(nextButton);
    form.addEventListener('submit',e=>{e.preventDefault();next();});
    form.append(label,hint,choices,input,error,footer);
    dialog.append(form);input.focus();
  }
  function drawBulk(){
    // One paste box for every open detail: the judge pastes once and the
    // text is split in the listed order. Extra paragraphs are appended to
    // the last answer, never dropped. Saving reuses the same validated
    // answer path as the single-question flow, and the summary step still
    // lets each answer be edited before it counts.
    const bulkError=node('p','error-message');bulkError.setAttribute('role','alert');
    dialog.append(node('p','field-help',`${state.questions.length} open ${state.questions.length===1?'detail':'details'} · paste everything in the order below, separated by a blank line`));
    const order=node('ol','product-bulk-order');
    for(const item of state.questions)order.append(node('li','',item.question));
    dialog.append(order);
    const area=node('textarea');area.id='bulk-all';area.rows=10;area.maxLength=6000;
    area.placeholder='Paste all details…';
    dialog.append(area,bulkError);
    const footer=node('div','dialog-actions');
    const save=node('button','primary','Save all details');save.type='button';
    footer.append(button('Back to questions','secondary',()=>{bulk=false;draw();}));
    footer.append(save);dialog.append(footer);
    save.addEventListener('click',async()=>{
      if(busy)return;busy=true;save.disabled=true;bulkError.textContent='';
      const chunks=area.value.split(/\n\s*\n/).map(part=>part.trim()).filter(Boolean);
      const updates={};
      state.questions.forEach((item,position)=>{
        let value=chunks[position] || '';
        if(position===state.questions.length-1 && chunks.length>state.questions.length)
          value=[value,...chunks.slice(state.questions.length)].filter(Boolean).join('\n\n');
        if(value){updates[item.id]=value;answers[item.id]=value;}
      });
      if(!Object.keys(updates).length){bulkError.textContent='Paste at least one detail, or go back to answer questions one by one.';busy=false;save.disabled=false;return;}
      try{
        await load(true,updates);
        if(closed)return;
        bulk=false;draw(true);
      }catch(e){if(!closed)bulkError.textContent=e.message;}
      finally{busy=false;if(!closed)save.disabled=false;}
    });
  }
  dialog.append(node('p','','Loading product questions…'));dialog.showModal();
  try{await load();if(!closed){index=Math.max(0,state.questions.findIndex(q=>!answers[q.id]));draw(!state.unanswered.length || (showSummary && state.questions.some(q=>answers[q.id])));}}
  catch(e){finish(null);showError(e);}
  return result;
}

async function editReviewInputs(){
  if(app.busy || productInterview.active)return;
  const wantsAI=app.project.ai_review?.status==='needs_context'?app.project.ai_review.resume_ai===true:app.config.key_configured;
  if(await interviewReviewInputs())await rethink({datasheets:false,ai:wantsAI?await confirmAIRun():false});
}

async function interviewReviewInputs(){
  if(productInterview.active)return false;
  const report=app.snapshot.report_id;
  if(!app.project.review_inputs?.count)return true;
  productInterview.active=true;
  const dialog=node('dialog','product-interview review-input-interview');dialog.setAttribute('aria-labelledby','review-input-title');
  document.body.append(dialog);
  let closed=false,busy=false,resolveResult,answered=0;
  const result=new Promise(resolve=>resolveResult=resolve);
  function finish(value){if(closed)return;closed=true;productInterview.active=false;dialog.close();dialog.remove();resolveResult(value);}
  dialog.addEventListener('cancel',e=>{e.preventDefault();if(!busy)finish(false);});
  dialog.addEventListener('close',()=>finish(false));
  function button(text,action,style='secondary'){const b=node('button',style,text);b.type='button';b.addEventListener('click',action);return b;}
  function draw(){
    if(closed)return;
    if(app.snapshot.report_id!==report){finish(false);showError(new Error('The project changed. Open its review questions again.'));return;}
    dialog.replaceChildren();
    const tasks=app.project.review_inputs?.tasks || [],task=tasks[0];
    const heading=node('div','dialog-heading'),title=node('h2','',task?'The review needs your answer':'Answers saved');title.id='review-input-title';
    heading.append(title,button('Pause for now',()=>{if(!busy)finish(false);}));dialog.append(heading);
    if(!task){
      dialog.append(node('p','','Continue to review your saved answers and recalculate the checks that can run. Unknown answers and physical tests stay clearly marked as needing verification.'),
        button('Continue review',()=>finish(true),'primary'));return;
    }
    dialog.append(node('p','field-help',`${tasks.length} ${tasks.length===1?'topic needs':'topics need'} your input. Each answer is saved with this project.`));
    dialog.append(node('h3','product-question',task.question),node('p','',task.hint));
    const error=node('p','error-message');error.setAttribute('role','alert');
    async function save(answer){
      if(busy || closed)return;
      if(!answer.trim()){error.textContent='Write an answer, or choose “I don’t know yet”.';return;}
      busy=true;for(const b of dialog.querySelectorAll('button'))b.disabled=true;
      try{
        await api('/api/question-answer',{report_id:report,context_revision:app.project.workspace.design_context.revision,
          question:task.question,answer,review_task:{id:task.id,signature:task.signature}});
        await refresh();answered++;draw();
      }catch(e){if(!closed){error.textContent=e.message;for(const b of dialog.querySelectorAll('button'))b.disabled=false;}}
      finally{busy=false;}
    }
    if(task.kind==='calculation'){
      dialog.append(calculationForm(task.section,'interview',{targets:task.targets,onBusy:value=>busy=value,onSaved:()=>{answered++;draw();}}));
      dialog.append(button('I don’t know these values yet',()=>save('Not sure yet. These operating limits still need to be established.')));
    }else{
      const form=node('form'),input=node('textarea');input.rows=4;input.maxLength=4000;input.required=true;input.setAttribute('aria-label',task.question);input.placeholder='Answer in your own words…';
      const actions=node('div','dialog-actions'),submit=node('button','primary','Save & next');submit.type='submit';
      actions.append(button('I don’t know yet',()=>save('Not sure yet. This check remains unresolved.')),submit);
      form.append(input,actions);form.addEventListener('submit',e=>{e.preventDefault();save(input.value.trim());});dialog.append(form);
    }
    dialog.append(error);dialog.scrollTop=0;
  }
  dialog.showModal();draw();return result;
}

function renderQuestionAnswers(box,onSave=()=>{}){
  const context=app.project?.workspace?.design_context || {};
  const saved=Object.values(context.question_answers || {});
  const questions=[...new Set([...(app.project?.saved_review_questions || app.project?.ai_review?.result?.questions || []),...saved.map(q=>q.question)])];
  if(!questions.length)return;
  box.append(node('h3','','Questions & saved answers'),node('p','field-help','Save each answer once. It stays with this project across updates and restarts. Saving does not spend AI credits.'));
  const report=app.snapshot.report_id;
  for(const question of questions){
    const existing=saved.find(q=>q.question.toLowerCase().trim()===question.toLowerCase().trim());
    // A model may acknowledge a saved answer in this list. Keep the note visible,
    // but do not ask the user to answer an explicit 'no question' statement.
    if(!existing && !question.includes('?') && /\bno (?:repeated|further|additional|new) questions?\b/i.test(question)){
      box.append(node('p','field-help',question));continue;
    }
    const form=node('form','product-answer'),label=node('label','',question),input=node('textarea');
    input.rows=2;input.maxLength=4000;input.value=existing?.answer || '';input.required=true;
    input.setAttribute('aria-label',question);input.placeholder='Your answer, or “Not sure yet”…';
    const save=node('button','secondary',existing?'Update saved answer':'Save answer');save.type='submit';
    const status=node('p','field-help',existing?'Saved to this project.':'');status.setAttribute('role','status');
    label.append(input);form.append(label,save,status);box.append(form);
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(save.disabled || !input.value.trim())return;
      if(app.snapshot.report_id!==report){status.textContent='Open this project again before saving.';return;}
      save.disabled=true;status.textContent='Saving…';
      try{
        const result=await api('/api/question-answer',{report_id:report,context_revision:app.project.workspace.design_context.revision,question,answer:input.value.trim()});
        app.project.workspace.design_context.revision=result.revision;
        onSave(result.revision);
        status.textContent='Saved. The next Vector review and repair request will use this answer.';
        save.textContent='Update saved answer';
        await refresh();
      }catch(error){status.textContent=error.message;}
      finally{save.disabled=false;}
    });
  }
}

// Free-text project context: what the device does, how controls behave, and
// design requirements. Saved with the project, editable any time, and read by
// analysis evidence and firmware discovery. Unsaved typing survives re-renders
// via the per-report draft.
const projectContextDraft={report:null,text:null,revision:0,saving:false};
async function renderProjectContextBox(container,reportId){
  if(!container || typeof api!=='function' || !reportId)return;
  if(projectContextDraft.report!==reportId){projectContextDraft.report=reportId;projectContextDraft.text=null;}
  const wrap=node('div','project-context-box');wrap.setAttribute('data-context-box',reportId);
  wrap.append(node('h3','','Project context'));
  const area=node('textarea');area.rows=4;area.maxLength=6000;
  area.placeholder='Describe what your project does, how its controls should behave, and any design requirements PreFlight should know.';
  area.setAttribute('aria-label','Project context');
  const row=node('div');const save=node('button','secondary','Save context');save.type='button';
  const status=node('span','field-help','');status.setAttribute('role','status');
  row.append(save,status);wrap.append(area,row);container.append(wrap);
  area.addEventListener('input',()=>{projectContextDraft.text=area.value;});
  try{
    const current=await api('/api/project-context',{report_id:reportId});
    projectContextDraft.revision=current.context_revision || 0;
    if(projectContextDraft.text===null)area.value=current.description || '';
    else area.value=projectContextDraft.text;
    if(current.description && projectContextDraft.text===null)status.textContent='Loaded saved context. Edit and save to update analysis and firmware discovery.';
    if(!current.description && projectContextDraft.text===null){
      try{
        const demos=((await api('/api/demo-projects')).demos || []).filter(d=>d.preset);
        for(const demo of demos.slice(0,4)){
          const load=node('button','text-button','Load “'+demo.name+'” preset');load.type='button';
          load.addEventListener('click',()=>{area.value=demo.preset;projectContextDraft.text=area.value;
            status.textContent='Demo preset loaded — edit freely, then save.';});
          row.append(load);
        }
      }catch{/* presets are optional */}
    }
  }catch(error){status.textContent=error.message || String(error);}
  save.addEventListener('click',async()=>{
    if(save.disabled || projectContextDraft.saving)return;
    if(app.snapshot && app.snapshot.report_id!==reportId){status.textContent='Open this project again before saving.';return;}
    save.disabled=true;projectContextDraft.saving=true;status.textContent='Saving…';
    try{
      const result=await api('/api/project-context',{report_id:reportId,description:area.value,context_revision:projectContextDraft.revision});
      projectContextDraft.revision=result.context_revision;projectContextDraft.text=null;
      if(app.project && app.project.workspace && app.project.workspace.design_context){
        app.project.workspace.design_context.description=result.description;
        app.project.workspace.design_context.revision=result.context_revision;
      }
      status.textContent='Saved. Future analysis runs and firmware discovery will use this context.';
    }catch(error){status.textContent=error.message || String(error);}
    finally{save.disabled=false;projectContextDraft.saving=false;}
  });
  return area;
}
