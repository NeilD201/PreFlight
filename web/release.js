'use strict';
const releaseSelections={};
const scopeFormState={signature:null,dirty:false,saving:false,drafts:{}};
const engineeringDrafts=new Map();

function renderCheckScope(){
  const home=$('checks-home');if(!home)return;
  const project=app.project,scope=project?.check_scope;
  const signature=JSON.stringify([app.snapshot?.report_id,scope?.signature,scope?.context_revision]);
  const validDrafts=new Set((project?.verification_results?.calculations || []).map(c=>app.snapshot.report_id+':'+c.signature));
  for(const key of engineeringDrafts.keys())if(!validDrafts.has(key))engineeringDrafts.delete(key);
  // Polling must not erase a user's unsaved answers or duplicate a save.
  if(scopeFormState.signature===signature && (scopeFormState.dirty || scopeFormState.saving || [...engineeringDrafts.keys()].some(k=>k.startsWith(app.snapshot.report_id+':'))))return;
  if(scopeFormState.signature!==signature){scopeFormState.drafts={};scopeFormState.dirty=false;}
  const openEngineering=new Set([...home.querySelectorAll('details[open]')].filter(el=>el.id?.startsWith('engineering-')).map(el=>el.id));
  scopeFormState.signature=signature;home.replaceChildren();
  home.append(node('h1','','What we check'),node('p','scope-intro',
    'First, establish what your product does. Then select the relevant checks. A selected check is not a passed check.'));
  if(!scope){home.append(node('p','','Open a project to see its check plan. Preflight currently checks saved connections, KiCad rules, selected part requirements and limited power calculations. EMC, ESD, crosstalk, thermal, return-path and transient simulations are not implemented.'));return;}
  home.append(node('p','field-help',`${project.provenance?.project_name || project.archive || 'Current project'} · current saved files and product context`));
  if(scope.description)home.append(node('p','scope-product',scope.description));
  const context=project.workspace?.design_context || project.design_context || {};
  const guided=node('button','primary','Ask questions for this project');guided.type='button';guided.disabled=app.busy;
  guided.addEventListener('click',()=>editProductContext(false).catch(showError));
  home.append(guided,node('p','field-help','Answer only missing details. Follow-up questions depend on your answers and the saved parts. The questions use no AI credits; the final review is a separate step.'));
  home.append(engineeringAnalysis());
  if(scope.memory_review?.devices?.length)home.append(memoryReviewPanel(scope.memory_review));
  for(const detail of home.querySelectorAll('details'))if(openEngineering.has(detail.id))detail.open=true;
  const form=node('form','scope-context panel');
  form.append(node('h2','','How will this product be used?'),node('p','field-help',
    'These answers select the review scope. Saving uses no AI credits. Unrecognized parts and external modules can be included here.'));
  for(const q of scope.questions){
    const field=node('div','scope-question'),select=node('select');select.id='scope-'+q.feature;select.name=q.id;
    const label=node('label','',q.question);label.htmlFor=select.id;
    const auto=node('option','','Use file evidence / not confirmed');auto.value='';select.append(auto);
    for(const [index,choice] of q.choices.entries()){const option=node('option','',q.choice_labels?.[index] || choice);option.value=choice;select.append(option);}
    select.value=q.answer || '';select.disabled=app.busy;
    select.addEventListener('change',()=>{scopeFormState.drafts[q.id]=select.value;scopeFormState.dirty=true;message.textContent='Answers changed. Save to update the check plan.';});
    const facts=node('details');facts.append(node('summary','','Why we ask'),node('p','',q.known),node('p','',q.hint));
    field.append(label,select,facts);form.append(field);
  }
  const save=node('button','primary','Save & update check plan');save.type='submit';save.disabled=app.busy;
  const message=node('p','field-help');message.setAttribute('role','status');form.append(save,message);
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(scopeFormState.saving || app.busy)return;
    const answers={};
    for(const q of scope.questions)answers[q.id]=form.elements.namedItem(q.id).value;
    scopeFormState.saving=true;save.disabled=true;
    for(const input of form.querySelectorAll('select'))input.disabled=true;
    message.textContent='Saving your product context…';
    try{
      await api('/api/check-scope',{report_id:app.snapshot.report_id,context_revision:context.revision,
        signature:scope.signature,answers});
      scopeFormState.dirty=false;scopeFormState.signature=null;await refresh();
    }catch(error){message.textContent=error.message;save.disabled=false;for(const input of form.querySelectorAll('select'))input.disabled=false;}
    finally{scopeFormState.saving=false;}
  });
  const setup=node('details','scope-setup');
  const unknowns=scope.questions.filter(q=>!q.answer || q.answer==='Not sure yet').length;
  setup.append(node('summary','',`Confirm product use${unknowns?' · '+unknowns+' answers not confirmed':''}`),form);
  home.append(setup,node('h2','','Selected for this product'),node('p','scope-limit',
    'These are review areas, not completed tests. Engineering analysis above provides geometry measurements and conditional electrical calculations. Full EMC/ESD immunity, coupled-field, antenna and thermal-field simulations are not implemented.'));
  const labels={relevant:'Relevant',needs_context:'Needs context',not_applicable:'Not selected'};
  const list=node('div','scope-check-list');
  for(const check of [...scope.checks,...(scope.special_reviews || [])]){
    const row=node('article','scope-check '+check.selection),heading=node('div','scope-check-heading');
    heading.append(node('h3','',check.title),node('span','scope-badge',labels[check.selection]));row.append(heading,node('p','',check.reason));
    const detail=node('details');detail.append(node('summary','','What the app can establish'),node('p','','Available: '+check.available),
      node('p','scope-limit','Not verified: '+check.limits));
    if(check.evidence?.length)detail.append(node('p','field-help','File evidence: '+check.evidence.join('; ')));
    row.append(detail);list.append(row);
  }
  home.append(list,node('p','field-help','Scope selection is automatic and local. Vector uses this plan when you next request a review; it must not turn missing context into an invented defect.'));
  const existing=node('section','scope-recorded');existing.append(node('h2','','Checks actually run on this revision'));
  const labelsRun={checked:'Checked',attention:'Needs review',partial:'Partly checked',missing:'Not verified'};
  for(const check of project.before_ordering?.checks || []){
    const detail=node('details');detail.append(node('summary','',`${check.title} — ${labelsRun[check.status] || 'Not verified'}`),node('p','',check.detail));existing.append(detail);
  }
  home.append(existing);
}

function memoryReviewPanel(review){
  const panel=node('details','release-card');panel.append(node('summary','','Memory interface review — not fully verified'));
  panel.append(node('p','','Connections are screened from saved pin roles. Memory timing and signal integrity need the exact controller, memory, speed and board stackup.'));
  for(const device of review.devices){
    const row=node('section','release-check');row.append(node('h3','',`${device.reference} · ${device.kind}`));
    row.append(node('p','',device.identity));
    const details=node('details');details.append(node('summary','',`${device.signals.length} identified signal pins`));
    for(const pin of device.signals)details.append(node('p','',`${pin.name} (pin ${pin.number}) → ${pin.no_connect?'marked unused':pin.net || 'no net'}${pin.peers?.length?' → '+pin.peers.join(', '):''}`));
    row.append(details);panel.append(row);
  }
  for(const observation of review.observations)panel.append(node('p','review-notice',observation.detail+' Check the selected bus mode before changing wiring.'));
  panel.append(node('h3','','Still to verify'));
  for(const requirement of review.requirements)panel.append(node('p','',requirement.title));
  const limits=node('details');limits.append(node('summary','','Scope and limits'));
  for(const limit of review.limits)limits.append(node('p','',limit));panel.append(limits);
  return panel;
}

function engineeringAnalysis(){
  const project=app.project,section=node('section','scope-recorded');
  section.append(node('h2','','Engineering analysis'),node('p','field-help','Local calculations and routing measurements. No AI credits. Results apply to the saved revision and the limits you enter; they do not certify the product.'));
  for(const [key,title] of [['thermal','Will a chip get too hot?'],['transient','Will a sudden load pull the supply too low?'],['trace_load','Will a trace drop too much voltage?'],['esd_margin','Does the proposed protection leave enough voltage margin?']]){
    const choices=(project.verification_results?.calculations || []).filter(c=>c.section===key);
    if(!choices.length)continue;
    const details=node('details','manufacturer-details');details.id='engineering-'+key;details.append(node('summary','',title),calculationForm(key,'scope'));section.append(details);
  }
  const geometry=project.board_checks?.signal_geometry,box=node('details','manufacturer-details');
  box.id='engineering-geometry';
  box.append(node('summary','','Parallel traces and ground beneath signals'));
  if(!geometry || !['measured','partial'].includes(geometry.status)){
    if(geometry?.message)box.append(node('p','field-help',geometry.message));
    box.append(node('p','','Measure the refilled PCB snapshot to find nearby parallel tracks and signal centerlines extending beyond adjacent ground fills. These are review candidates, not proven signal failures.'));
    const button=node('button','secondary','Measure saved board');button.type='button';button.disabled=app.busy;
    const message=node('p','field-help');let pending=false;
    button.addEventListener('click',async()=>{
      if(pending || app.busy)return;pending=true;button.disabled=true;message.textContent='Measuring the saved board locally…';
      try{await api('/api/engineering-geometry',{report_id:app.snapshot.report_id});await refresh();}
      catch(error){message.textContent=error.message;button.disabled=false;}finally{pending=false;}
    });box.append(button,message);
  }else{
    box.append(node('p','',`${geometry.straight_tracks} straight tracks screened · ${geometry.parallel_count} nearby parallel pairs · ${geometry.status==='partial'?'Partial geometry coverage':'Geometry measured'}`));
    const gaps=(geometry.reference_coverage || []).filter(r=>r.uncovered_mm>0.000001);
    const ground=node('details');ground.append(node('summary','',`Ground-fill gaps · ${gaps.length} net/layer combinations`));
    ground.append(node('p','field-help','This compares signal centerlines with named ground fills on adjacent layers. A gap may have another valid return path; zero gaps does not prove a continuous grounded reference or acceptable RF behavior.'));
    for(const r of gaps)ground.append(node('p','',`${r.net} on ${r.signal_layer}: ${r.uncovered_mm.toFixed(3)} mm of ${r.length_mm.toFixed(3)} mm lies outside ${r.reference_net} fill on ${r.reference_layer}.`));
    const pairs=node('details');pairs.append(node('summary','',`Nearby parallel tracks · ${geometry.parallel_count} pairs`));
    for(const p of geometry.parallel_pairs || []){
      const item=node('details');item.append(node('summary','',`${p.nets.join(' ↔ ')} · ${p.layer} · gap ${p.gap_mm.toFixed(3)} mm · overlap ${p.parallel_mm.toFixed(3)} mm`),node('p','field-help',p.interpretation));
      for(const t of p.tracks)item.append(node('p','field-help',`${t.net}: (${t.start.join(', ')}) → (${t.end.join(', ')}) mm · UUID ${t.uuid || 'not available'}`));pairs.append(item);
    }
    if(geometry.parallel_details_omitted)pairs.append(node('p','field-help',`${geometry.parallel_details_omitted} additional pair details omitted from this view.`));
    box.append(ground,pairs);for(const line of geometry.limits || [])box.append(node('p','field-help',line));
  }
  section.append(box);return section;
}

function calculationForm(section,location='release',options={}){
  const results=app.project.verification_results || {revision:0,calculations:[]};
  const choices=results.calculations.filter(c=>c.section===section && (!options.targets || options.targets.includes(c.key)));
  if(!choices.length)return node('p','field-help','Run Update to identify the connections needed for this calculation.');
  const wrap=node('div','verification-calculator');
  const select=node('select');select.id='verification-target-'+location+'-'+section;
  const label=node('label','',section==='power'?'Power rail':section==='thermal'?'Component':'Connection');label.htmlFor=select.id;
  for(const c of choices){const option=node('option','',c.label+' · '+({checked:'Within limits',attention:'Outside limits',missing:c.stale?'Design changed':'Needs limits'})[c.status]);option.value=c.key;select.append(option);}
  if(choices.some(c=>c.key===releaseSelections[section]))select.value=releaseSelections[section];
  const body=node('div');wrap.append(label,select,body);
  function show(){
    body.replaceChildren();const c=choices.find(c=>c.key===select.value);releaseSelections[section]=c.key;
    const draftKey=app.snapshot.report_id+':'+c.signature,draft=engineeringDrafts.get(draftKey);
    body.append(node('p','field-help',c.description));
    if(c.kind==='i2c')body.append(node('p','field-help','Rise-time limits: Standard mode 1000 ns; Fast mode 300 ns; Fast-mode Plus 120 ns. Use the limits specified for your devices and selected speed.'));
    const result=node('p','verification-result '+(draft?'missing':c.status),draft?'Inputs changed. Calculate to update the result.':c.detail);result.setAttribute('role','status');body.append(result);
    const form=node('form');const fields=node('div','verification-fields');
    for(const [index,f] of c.fields.entries()){
      const group=node('div');const input=node('input');input.id='verification-'+location+'-'+section+'-'+index;input.name=f.key;
      const suggested=c.suggested_values?.[f.key];
      input.type='number';input.step='any';input.min=String(f.minimum);input.required=true;input.value=draft?.values[f.key] ?? c.values?.[f.key] ?? suggested?.value ?? '';
      const lab=node('label','',f.label+' ('+f.unit+')');lab.htmlFor=input.id;group.append(lab,input);fields.append(group);
      if(suggested)group.append(node('p','field-help',suggested.basis+' '+suggested.qualification));
    }
    const note=node('textarea');note.id='verification-note-'+location+'-'+section;note.name='note';note.required=true;note.maxLength=2000;note.rows=3;note.value=draft?.note ?? c.note ?? '';
    const noteLabel=node('label','','Operating case and source of these numbers');noteLabel.htmlFor=note.id;
    note.placeholder='For example: peak load with all sensors active; maximum currents from the selected parts’ datasheets.';
    const message=node('p','field-help');message.setAttribute('role','status');
    const actions=node('div','release-downloads');
    const save=node('button','primary','Calculate & save limits');save.type='submit';save.dataset.designAction='verification';save.disabled=app.busy;
    const clear=node('button','secondary','Clear saved limits');clear.type='button';clear.dataset.designAction='verification';clear.disabled=app.busy || !Object.keys(c.values || {}).length;
    const confirmation=node('label','check-option'),confirmed=node('input');confirmed.type='checkbox';confirmed.name='confirm_suggestions';
    confirmation.append(confirmed,node('span','','I checked the suggested values against my design. A nominal voltage is not necessarily its maximum.'));
    actions.append(save,clear);form.append(fields);
    if(Object.keys(c.suggested_values || {}).length)form.append(confirmation);
    form.append(noteLabel,note,actions,message);body.append(form);
    form.addEventListener('input',()=>{result.textContent='Inputs changed. Calculate to update the result.';result.className='verification-result missing';
      engineeringDrafts.set(draftKey,{values:Object.fromEntries(c.fields.map(f=>[f.key,form.elements.namedItem(f.key).value])),note:note.value});});
    let submitting=false;
    async function submit(clearSaved){
      if(submitting || app.busy)return;submitting=true;
      options.onBusy?.(true);
      const values={};for(const f of c.fields)values[f.key]=Number(form.elements.namedItem(f.key).value);
      for(const input of form.querySelectorAll('input,textarea,button'))input.disabled=true;select.disabled=true;
      message.textContent='Calculating…';
      try{
        await api('/api/verification',{report_id:app.snapshot.report_id,revision:results.revision,target:c.key,signature:c.signature,values,note:note.value,clear:clearSaved,confirm_suggestions:confirmed.checked});
        engineeringDrafts.delete(draftKey);scopeFormState.signature=null;
        await refresh();
        options.onSaved?.();
      }catch(error){message.textContent=error.message;for(const input of form.querySelectorAll('input,textarea,button'))input.disabled=false;select.disabled=false;}finally{submitting=false;options.onBusy?.(false);}
    }
    form.addEventListener('submit',event=>{event.preventDefault();submit(false);});clear.addEventListener('click',()=>submit(true));
    if(c.evidence?.length){const details=node('details');details.append(node('summary','','Calculation and scope'));const list=node('ul');for(const line of c.evidence)list.append(node('li','',line));details.append(list);body.append(details);}
  }
  select.addEventListener('change',show);show();return wrap;
}

function initializeRelease(tabs){
  const button=node('button','','Overview');button.id='tab-release';button.dataset.tab='release';
  button.setAttribute('role','tab');button.setAttribute('aria-controls','panel-release');tabs.prepend(button);
  const panel=node('section','release-panel');panel.id='panel-release';panel.hidden=true;
  panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby','tab-release');$('results').append(panel);
}

function releaseBoardOverview(project){
  const hero=node('section','release-hero');
  const board=node('div','release-board');
  board.append(node('span','preview-label','Saved design · Front copper'));
  const visual=project.workspace?.board_visual,front=visual?.views?.front;
  if(visual?.status==='ready' && front?.file){
    const image=node('img');image.src=`/reports/${encodeURIComponent(app.snapshot.report_id)}/${front.file}`;
    image.alt='Saved board preview, front copper and component outlines';
    const fallback=node('span','preview-unavailable','Board preview unavailable');fallback.hidden=true;
    image.addEventListener('error',()=>{image.hidden=true;fallback.hidden=false;},{once:true});
    board.append(image,fallback);
  }else board.append(node('span','preview-unavailable','Update the review to create a board preview.'));
  const foot=node('div','board-preview-foot');
  foot.append(node('span','',`${Object.keys(project.components || {}).length} components`),node('span','','KiCad · saved revision'));
  board.append(foot);
  const decision=node('div','release-decision');
  const assignments=project.review_sections?.assignments || {};
  const groups=(project.review?.groups || []).filter(group=>assignments[group.id]?.attention);
  const count=groups.length;
  const incomplete=(project.before_ordering?.checks || []).filter(check=>['partial','missing'].includes(check.status)).length;
  decision.append(node('span','kicker','Release review'),
    node('h2','',count?`${count} ${count===1?'finding':'findings'} in the saved design.`:incomplete?'Analysis has remaining gaps.':'Your saved results.'),
    node('p','',count?'These are the findings identified so far, not a complete count of board faults. Tab counts match this list; unfinished checks are under Check coverage.':incomplete?'No actionable finding is listed, but verification is incomplete. Open Check coverage below to see what is missing.':'No actionable finding is listed. Review check coverage and production files before ordering.'));
  const action=node('button','primary',count?'See findings →':incomplete?'See analysis coverage ↓':'Open production files ↓');
  action.type='button';
  action.addEventListener('click',()=>{
    if(count){selectTab(assignments[groups[0].id].section);openOverviewIssue(groups[0].id);}
    else {
      const target=document.querySelector(incomplete?'.release-coverage':'.release-exports');
      if(incomplete && target)target.open=true;
      target?.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
    }
  });
  decision.append(action,node('p','field-help','This is a review of saved files. Update after saving in KiCad.'));
  hero.append(board,decision);return hero;
}

const reviewAttention = {seen:new Set(), dialog:null, key:null};
function maybeShowReviewAttention(){
  const p=app.project, assignments=p?.review_sections?.assignments || {};
  const groups=(p?.review?.groups || []).filter(g=>assignments[g.id]?.attention);
  const key=JSON.stringify([app.snapshot.report_id,groups.map(g=>[g.id,g.signature || '',assignments[g.id].status])]);
  if(reviewAttention.dialog && reviewAttention.key!==key)reviewAttention.dialog.close();
  if(!p || p.ai_review?.status!=='completed' || p.review_inputs?.count || p.review_inputs?.needs_product_profile || !groups.length || app.busy || app.pending || app.adding || app.view!=='workspace' || $('workspace-view').hidden)return;
  if(reviewAttention.seen.has(key) || document.querySelector('dialog[open]'))return;
  const dialog=node('dialog','review-attention-dialog');
  dialog.setAttribute('aria-labelledby','review-attention-title');
  dialog.setAttribute('aria-describedby','review-attention-description');
  const heading=node('h2','','Your analysis results');
  heading.id='review-attention-title';
  const description=node('p','', 'Preflight has analyzed the saved design. Open a result for its explanation, proposed change or manufacturing setting. Items without a supported conclusion are marked Analysis incomplete.');
  description.id='review-attention-description';
  const list=node('ul','review-attention-list');
  const open=group=>{dialog.close();navigate('workspace');selectTab(assignments[group.id].section);openOverviewIssue(group.id);};
  for(const group of groups.slice(0,5)){
    const action=p.action_plan?.actions?.find(a=>a.group_id===group.id);
    const row=node('li'),button=node('button','',issueTitle(group,action));button.type='button';
    button.append(node('small','result-state',findingLabel(group,action)));
    button.addEventListener('click',()=>open(group));row.append(button);list.append(row);
  }
  if(groups.length>5)list.append(node('li','field-help',`And ${groups.length-5} more in the review.`));
  const actions=node('div','review-attention-actions');
  const start=node('button','primary','See results');start.type='button';start.autofocus=true;
  start.addEventListener('click',()=>open(groups[0]));
  const later=node('button','secondary','Not now');later.type='button';later.addEventListener('click',()=>dialog.close());
  actions.append(start,later);
  dialog.append(node('span','kicker',p.provenance?.project_name || p.archive || 'Your board'),heading,description,list,actions,
    node('p','field-help','File changes require your approval. The app does not treat incomplete analysis as a verified result.'));
  dialog.addEventListener('close',()=>{dialog.remove();if(reviewAttention.dialog===dialog)reviewAttention.dialog=null;});
  document.body.append(dialog);dialog.showModal();
  reviewAttention.dialog=dialog;reviewAttention.key=key;reviewAttention.seen.add(key);
}

function releaseIssueQueue(project){
  const box=node('section','release-issue-queue');box.append(node('h2','','Needs attention'));
  const assignments=project.review_sections?.assignments || {};
  const groups=(project.review?.groups || []).filter(g=>assignments[g.id]?.attention);
  if(!groups.length)box.append(node('p','','No actionable findings listed. This does not mean every check passed.'));
  for(const group of groups){
    const action=project.action_plan?.actions?.find(a=>a.group_id===group.id);
    const row=node('article','release-issue-row'),copy=node('div');
    copy.append(node('small','',(group.components || []).join(', ') || 'Whole board'),node('h3','',issueTitle(group,action)),node('span','finding-state',findingLabel(group,action)));
    const actions=node('div','release-issue-actions');
    const open=node('button','secondary','View issue');open.type='button';open.addEventListener('click',()=>{selectTab(assignments[group.id].section);openOverviewIssue(group.id);});actions.append(open);
    const fix=fileFixEntry(group,action);if(fix)actions.append(fix);
    const design=(typeof coordinatedFixEntry==='function'?coordinatedFixEntry(group,action):null);if(design)actions.append(design);
    const chat=node('button','text-button','Issue chat');chat.type='button';chat.addEventListener('click',()=>openIssueChat(group.id));actions.append(chat);
    row.append(copy,actions);box.append(row);
  }
  const notes=node('button','text-button','Reference notes — library differences, labels and reviewed items');notes.type='button';notes.addEventListener('click',()=>selectTab('notes'));box.append(notes);
  const details=node('button','text-button','Answer questions about your product');details.type='button';details.addEventListener('click',()=>editProductContext().catch(showError));box.append(details);
  return box;
}

function releaseCheckState(row){
  if(row.status==='checked')return {kind:'passed',label:row.calculation_section?'Within your entered limits':'Passed this check'};
  if(row.status==='attention')return {kind:'attention',label:'Needs a closer look'};
  if(row.requirement==='physical')return {kind:'physical',label:'Needs a hands-on check'};
  if(row.requirement==='limits')return engineeringExperience()?{kind:'input',label:'Needs your input'}:{kind:'review',label:'Needs engineering analysis'};
  return {kind:'review',label:'Needs more review'};
}
function engineeringExperience(){
  if(app.project?.explanation_profile?.experience_level)return app.project.explanation_profile.experience_level==='technical';
  const context=app.project?.workspace?.design_context || app.project?.design_context || {};
  return context.product_context?.experience==='Experienced EE — show electrical limits';
}
function releaseSectionSummary(rows){
  const count=kind=>rows.filter(row=>releaseCheckState(row).kind===kind).length;
  return [count('passed')?`${count('passed')} passed`:null,
    count('attention')?`${count('attention')} ${count('attention')===1?'needs':'need'} a closer look`:null,
    count('input')?`${count('input')} ${count('input')===1?'needs':'need'} your input`:null,
    count('review')?`${count('review')} ${count('review')===1?'needs':'need'} more review`:null,
    count('physical')?`${count('physical')} ${count('physical')===1?'needs a hands-on check':'need hands-on checks'}`:null].filter(Boolean).join(' · ') || 'No checks in this section';
}

function renderRelease(){
  const panel=$('panel-release'),p=app.project;if(!panel || !p)return;
  const openCards=new Set([...panel.querySelectorAll('details[open]')].map(el=>el.id).filter(Boolean));
  panel.replaceChildren();
  const review=p.before_ordering;
  if(!review){panel.append(node('p','','Refresh the app to load the manufacturing review.'));return;}
  const heading=node('header','release-heading');
  heading.append(node('span','kicker','Board overview'),node('h2','','Your review'));
  if(p.review_inputs?.count || p.review_inputs?.needs_product_profile || p.ai_review?.status==='needs_context'){
    const prompt=node('div','review-notice');prompt.append(node('strong','','Your review needs answers'),node('p','','Answer the questions here to continue. Each answer is saved; unknowns stay unresolved.'));
    const answer=node('button','primary','Continue review questions');answer.type='button';answer.disabled=app.busy;
    answer.addEventListener('click',()=>{(p.review_inputs?.needs_product_profile || p.ai_review?.status==='needs_context'?editProductContext(false):editReviewInputs()).catch(showError);});prompt.append(answer);heading.append(prompt);
  }
  const ai=p.ai_review || {},aiNotice=node('div','review-notice');
  if(app.snapshot.job?.kind==='review' && app.snapshot.job.status==='running'){
    aiNotice.append(node('strong','','Vector review in progress'),node('p','','Your previous local checks remain available. Follow the current request in the activity log.'));heading.append(aiNotice);
  }else if(['failed','stale','disabled','not_configured'].includes(ai.status)){
    const detail=reviewFailureNotice(ai);
    aiNotice.append(node('strong','',ai.status==='disabled'?'Vector review is off for this saved run':detail.title),node('p','',detail.message),aiRetryButton());
    const log=node('button','text-button','View review log');log.type='button';log.addEventListener('click',()=>selectTab('ai'));aiNotice.append(log);
    heading.append(aiNotice);
  }else if(ai.status==='completed'){
    const label=ai.rejected_items?.length?`Vector review · ${ai.rejected_items.length} conclusion${ai.rejected_items.length===1?' needs':'s need'} more evidence`:'Vector review completed';
    const log=node('button','text-button',label);log.type='button';log.addEventListener('click',()=>selectTab('ai'));heading.append(log);
  }
  const summary=node('div','release-summary');
  const remaining=review.checks.filter(r=>['partial','missing'].includes(r.status));
  for(const [label,n,cls] of [['Passed checks',review.summary.checked || 0,'checked'],['Need a closer look',review.summary.attention || 0,'attention'],['Need input or more review',remaining.filter(r=>r.requirement!=='physical').length,'missing'],['Need hands-on checks',remaining.filter(r=>r.requirement==='physical').length,'missing']]){
    const metric=node('div',cls);metric.append(node('strong','',String(n)),node('span','',label));summary.append(metric);
  }
  const downloads=node('div','release-downloads');
  const report=encodeURIComponent(app.snapshot.report_id);
  if(p.fabrication?.status==='draft'){
    for(const [name,label] of [['fabrication-draft.zip','Gerber & drill draft'],['analyzed-board.kicad_pcb','Checked PCB copy']]){
      const link=node('a',name.endsWith('.zip')?'button primary':'button secondary',label);link.href=`/reports/${report}/${name}`;link.download=name;downloads.append(link);
    }
  }else{
    const update=node('button','primary','Update checks & production files');update.type='button';update.dataset.updateProject='release';update.disabled=app.busy;
    update.addEventListener('click',updateSavedProject);downloads.append(update);
  }
  const checklist=node('a','button secondary','Download review checklist');checklist.href=`/api/checklist/${report}`;checklist.download='before-ordering.txt';downloads.append(checklist);
  panel.append(heading,releaseBoardOverview(p),releaseIssueQueue(p));
  if(typeof firmwarePanel==='function'){const firmware=firmwarePanel(p);if(firmware)panel.append(firmware);}
  const coverage=node('details','release-coverage');coverage.id='release-check-coverage';coverage.open=openCards.has(coverage.id);
  const unfinished=review.checks.filter(r=>r.status!=='checked').length;
  coverage.append(node('summary','',`Check coverage · ${unfinished?'Verification incomplete':'Recorded checks complete'}`),
    node('p','field-help','These are verification tasks, not extra board faults. Findings needing attention are listed above.'),summary);
  const grid=node('div','release-grid');
  for(const [id,label] of Object.entries(review.sections)){
    const rows=review.checks.filter(r=>r.section===id);
    const card=node('details','release-card');card.id='release-'+id;
    card.open=openCards.has(card.id);
    const title=node('summary');const text=node('span');text.append(node('strong','',label));
    const needs=rows.filter(r=>r.status==='attention').length,unverified=rows.filter(r=>['missing','partial'].includes(r.status)).length;
    text.append(node('small','',releaseSectionSummary(rows)));
    title.append(text,node('span',`release-dot ${needs?'attention':unverified?'missing':'checked'}`));card.append(title);
    for(const row of [...rows].sort((a,b)=>(a.status==='checked')-(b.status==='checked'))){
      const item=node('section','release-check');const header=node('div','release-check-title');header.append(node('h3','',row.title));
      header.append(node('span','release-badge '+row.status,releaseCheckState(row).label));item.append(header);
      const next=node('p');const instruction=row.requirement==='limits' && row.status!=='checked' && !engineeringExperience()
        ?'Describe how the product will be used in Product details. The app uses that context and saved evidence; missing electrical limits remain unverified.'
        :reviewInstruction(row.next_step);
      next.append(node('strong','',row.status==='checked'?'Keep in mind: ':'Next: '),node('span','',instruction));item.append(next);
      if(row.calculation_section){
        const inputs=node('details','release-calculation');inputs.id='release-inputs-'+row.id;inputs.open=openCards.has(inputs.id);
        inputs.append(node('summary','',engineeringExperience()?'Review suggested values & enter missing limits':'Engineering details (optional)'),calculationForm(row.calculation_section));item.append(inputs);
      }
      const proof=node('details','release-evidence');proof.append(node('summary','','What the app checked'),node('p','',reviewInstruction(row.detail)));
      if(row.evidence?.length){const list=node('ul');for(const e of row.evidence)list.append(node('li','',e));proof.append(list);}item.append(proof);
      if(row.id==='rule_scope' && p.board_checks?.expanded_rule_review?.status==='completed'){
        const links=node('div','release-downloads');
        for(const result of p.board_checks.expanded_rule_review.results || [])if(result.rules?.length){const link=node('a','text-link',result.kind.toUpperCase()+' additional rule report');link.href=`/reports/${report}/kicad-expanded-${result.kind}.json`;link.download='kicad-expanded-'+result.kind+'.json';links.append(link);}item.append(links);
      }
      const open=node('button','text-link',({connections:'Open Circuit',placement:'Open PCB layout',manufacturing:'Open Manufacturer',assembly:'Open Assembly',traces:'Open Signals',project:'Open Project',coverage:'Open rule findings'})[row.target] || 'Open details');
      open.type='button';open.addEventListener('click',()=>selectTab(row.target));item.append(open);card.append(item);
    }
    grid.append(card);
  }
  const exports=node('section','release-exports');
  exports.append(node('h3','','Production files'),downloads,node('p','release-note',p.fabrication?.status==='draft'?'Draft files use the refilled board copy. Review the checks above and your manufacturer’s preview before ordering.':'Update the review to generate production files from the saved project.'));
  coverage.append(grid);panel.append(coverage,exports,node('p','field-help',review.note));
}
