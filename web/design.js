'use strict';

const designUI = {scope:null, description:null, operatingContext:null, notes:new Map(), focusKey:null,
  chatReport:null, chatScope:null, chatGroup:null, chatStep:0, chatMessages:[], chatBusy:false, chatDrafts:new Map(), chats:new Map()};

function chatReviewScope() {
  return JSON.stringify({report:app.snapshot?.report_id || null,
    sources:app.project?.provenance?.source_hashes || {},
    context:app.project?.workspace?.design_context?.revision ?? app.project?.design_context?.revision ?? 0});
}
function chatConversationKey(groupId=designUI.chatGroup,step=designUI.chatStep) {
  const group=app.project?.review?.groups?.find(item=>item.id===groupId);
  const evidence=group ? [group.key,group.signature,group.title,group.summary,group.evidence] : [];
  return JSON.stringify([chatReviewScope(),groupId,evidence]);
}

async function retryAIReview(connectionsOnly=false) {
  if(app.busy || app.pending || !app.project)return;
  if(!app.config.key_configured){openSettings();return;}
  clearError();app.pending=true;setBusy();
  try{
    await api('/api/review-ai',{report_id:app.snapshot.report_id,context_intake:!connectionsOnly,connections_only:connectionsOnly===true});
    await refresh();
    return true;
  }catch(error){showError(error);return false;}
  finally{app.pending=false;setBusy();}
}

const automaticReview = {attempted:new Set(), dialog:null, stage:null, shown:new Set()};
function automaticReviewKey(){
  const p=app.project,context=p.workspace?.design_context || p.design_context || {};
  return JSON.stringify([p.provenance?.schematic_uuid || p.archive,
    Object.entries(p.provenance?.source_hashes || {}).sort(),context.revision || 0,app.config.model]);
}
function analysisStage(key,title,message,retry=false,runLabel=null){
  if(automaticReview.stage===key){
    if(automaticReview.dialog)automaticReview.dialog.querySelector('p').textContent=message;
    return;
  }
  automaticReview.dialog?.close();
  if(automaticReview.shown.has(key) || document.querySelector('dialog[open]'))return;
  automaticReview.stage=key;
  const dialog=node('dialog','review-attention-dialog');dialog.setAttribute('aria-labelledby','analysis-stage-title');
  const heading=node('h2','',title);heading.id='analysis-stage-title';
  const text=node('p','',message);text.setAttribute('role','status');
  dialog.append(node('span','kicker','Preflight analysis'),heading,text);
  const controls=node('div','review-attention-actions');
  if(retry){const button=node('button','primary',runLabel || 'Retry analysis');button.type='button';button.addEventListener('click',()=>{dialog.close();retryAIReview();});controls.append(button);}
  const browse=node('button','secondary','Continue browsing');browse.type='button';browse.addEventListener('click',()=>dialog.close());controls.append(browse);
  dialog.append(controls,node('p','field-help',retry?'Saved local results are still available. No files were changed.':'Preflight does the analysis. If it needs a fact about your product, it will ask you directly.'));
  dialog.addEventListener('close',()=>{dialog.remove();if(automaticReview.dialog===dialog)automaticReview.dialog=null;});
  document.body.append(dialog);dialog.showModal();automaticReview.dialog=dialog;automaticReview.shown.add(key);
}
function maybeReviewSavedProject(){
  if(!app.project || app.view!=='workspace' || app.adding)return false;
  const job=app.snapshot.job,ai=app.project.ai_review || {};
  if(job?.status==='running' && ['analysis','review','rethink'].includes(job.kind)){
    analysisStage('job:'+job.id,'Preflight is analyzing your board',job.message || 'Comparing the saved connections, datasheets and product context.');return true;
  }
  if(app.pending || app.busy)return false;
  if(automaticReview.dialog && !automaticReview.stage?.startsWith('failed:'))automaticReview.dialog.close();
  if(app.project.workspace?.chat_context_handled)return false; // Chat already used its bounded recheck; no hidden second job.
  if(ai.status==='needs_context' || app.project.review_inputs?.count || app.project.review_inputs?.needs_product_profile)return false;
  const key=automaticReviewKey();
  if(ai.status==='failed'){
    analysisStage('failed:'+key,'Preflight could not finish the analysis',ai.message || 'The analysis did not produce a usable result.',true);return true;
  }
  // Respect explicitly disabled AI. A completed partial analysis is also not an
  // invitation to keep spending credits; its limitations remain visible.
  if(!['stale','not_configured','not_requested','not_run'].includes(ai.status) || !app.config.key_configured || app.config.keychain_loading)return false;
  if(app.project.workspace?.source_status?.status==='changed' || document.querySelector('dialog[open]'))return false;
  let previous=false;try{previous=sessionStorage.getItem('preflight-auto-review:'+key)==='asked';}catch{}
  if(previous || automaticReview.attempted.has(key))return false;
  automaticReview.attempted.add(key);
  try{sessionStorage.setItem('preflight-auto-review:'+key,'asked');}catch{}
  // Paid runs need an explicit press. Ask once with the cost stated instead of
  // auto-starting — especially right after the user paused or dismissed a flow.
  analysisStage('starting:'+key,'Run the Vector review?','Your saved design and answers are ready. Running the review uses the connected AI provider and counts against your existing request limits.',true,'Run Vector review');
  return true;
}

function aiRetryButton() {
  const changed=app.project?.workspace?.source_status?.status==='changed';
  const status=app.project?.ai_review?.status;
  const label=status==='failed'?'Retry Vector review':status==='completed'?'Review again':'Run Vector review';
  const button=node('button','primary',changed?'Update changed files':app.config.key_configured?label:'Connect Vector');
  button.type='button';button.dataset.designAction='review-ai';button.disabled=app.busy || app.pending;
  button.addEventListener('click',()=>changed?updateSavedProject():retryAIReview());
  return button;
}

async function rethink(change={}) {
  // Saving context is always free: this path only runs local reconsideration
  // (plan, quotes, checks). A paid Vector investigation starts only from the
  // explicit review buttons (retryAIReview / updateSavedProject), which state
  // the AI cost before sending anything.
  if(app.busy)return;
  clearError();app.pending=true;setBusy();
  const context=app.project.workspace.design_context;
  try {
    await api('/api/rethink',{
      report_id:app.snapshot.report_id,context_revision:context.revision,context_intake:true,
      description:$('device-context').value,
      operating_context:Object.fromEntries(['interfaces','power','radio','fabrication'].map(key=>[key,$('context-'+key)?.value || ''])),
      ai:false,datasheets:$('lookup-datasheets').checked,...change
    });
    designUI.description=null;designUI.operatingContext=null;
    if(change.intention){designUI.notes.delete(change.intention.key);designUI.focusKey=change.intention.key;$('plan-filter').value='all';}
    await refresh();
  }catch(error){showError(error);}
  finally{app.pending=false;setBusy();}
}

function renderDesign() {
  const p=app.project, context=p.workspace.design_context, plan=p.action_plan, box=$('design-panel');
  const scope=JSON.stringify([p.provenance?.project_name || p.archive,p.provenance?.schematic_uuid || p.selected_files]);
  if(scope!==designUI.scope){designUI.scope=scope;designUI.description=null;designUI.operatingContext=null;designUI.notes.clear();}
  const oldLookup=$('lookup-datasheets')?.checked ?? false;
  box.replaceChildren();
  const header=node('div','design-heading');header.append(node('h2','','Device context'));box.append(header);
  const product=node('button','secondary','Questions from the files');product.type='button';product.disabled=app.busy;
  product.addEventListener('click',()=>editProductContext().catch(showError));
  box.append(product,node('p','field-help',Object.keys(context.product_context || {}).length
    ? 'Your product answers are saved with this project and included in reviews, step help and repair planning.'
    : 'Questions are tied to the components and connections in this saved project. The questions use no API credits.'));
  const description=node('details','device-interpretation');description.append(node('summary','','What the app understands'),node('p','device-description',plan.device.description),node('small','field-help',plan.device.basis));box.append(description);
  const basis=node('details','design-basis');basis.append(node('summary','','What this interpretation is based on'));
  const chips=node('div','chips');for(const id of plan.device.evidence_ids || [])chips.append(evidenceLink(id));basis.append(chips);list(basis,plan.device.assumptions || []);box.append(basis);
  const editor=node('details','context-editor');editor.append(node('summary','','Edit intended use & operating details'));
  const form=node('form','context-form'),label=node('label','','What should the review understand?'),input=node('textarea');
  input.id='device-context';input.rows=3;input.maxLength=6000;label.htmlFor=input.id;
  input.placeholder='For example: A wrist-worn prototype. BLE sends raw sensor data to a phone. The display and battery are off-board. Charging is limited to 100 mA.';
  input.value=designUI.description ?? context.description;
  input.addEventListener('input',()=>{designUI.description=input.value;});
  const controls=node('div','context-controls'),lookup=node('label','lookup-toggle'),check=node('input');check.type='checkbox';check.id='lookup-datasheets';check.checked=oldLookup;lookup.append(check,node('span','','Find and read LCSC datasheets'));
  const update=node('button','primary','Save & review');update.type='submit';update.dataset.designAction='update';update.disabled=app.busy;controls.append(lookup,update);
  form.append(label,input,controls);
  const shared=node('div','operating-context');
  shared.append(node('p','field-help','Used by Circuit and PCB & signals. Add known requirements; leave unknown values blank.'));
  for(const [key,title,example] of [
    ['interfaces','Interfaces & operating modes','For example: The sensors use I²C. GPIO1 is an input. ECG electrodes connect through J2.'],
    ['power','Power source & load','For example: One Li-ion cell; USB charging. Expected peak current, rail voltages, and acceptable voltage drop if known.'],
    ['radio','Antenna & sensitive signals','For example: A 2.4 GHz antenna attaches to J3. Describe any external antenna or sensitive signal source.'],
    ['fabrication','Board manufacturer & assembly','For example: The exact board service, copper weight and minimum spacing you are ordering.']]){
    const field=node('div'),name=node('label','',title),value=node('textarea');name.htmlFor='context-'+key;value.id=name.htmlFor;value.rows=2;value.maxLength=1500;value.placeholder=example;
    value.value=(designUI.operatingContext || context.operating_context || {})[key] || '';
    value.addEventListener('input',()=>{designUI.operatingContext={...(designUI.operatingContext || context.operating_context || {}),[key]:value.value};});field.append(name,value);shared.append(field);
  }
  form.insertBefore(shared,controls);
  form.append(node('p','field-help',app.config.key_configured?'Part names go to LCSC. Vector receives circuit evidence, your context and selected datasheet excerpts.':'No provider key is connected. You can fetch datasheets and save context; connect Vector for Vector reconsideration.'));
  form.addEventListener('submit',e=>{e.preventDefault();rethink();});editor.append(form);box.append(editor);
  const ai=p.ai_review || {};
  if(['failed','stale'].includes(ai.status)){
    const message=node('div','review-notice');message.append(node('strong','',ai.status==='stale'?'Context needs a new Vector review':'Vector review could not finish'),node('p','',ai.message));box.append(message);
  }
  if(!app.config.key_configured){const b=node('button','text-button','Connect Vector');b.type='button';b.addEventListener('click',openSettings);box.append(b);}
}

function intentEditor(action) {
  const form=node('form','intent-editor'),label=node('label','','Is this intentional? Explain why.'),input=node('textarea');
  input.id='intent-'+action.group_id;input.rows=2;input.maxLength=3000;label.htmlFor=input.id;
  input.placeholder='Describe the intended behavior, external circuitry, operating mode or manufacturing agreement.';
  input.value=designUI.notes.get(action.group_key) ?? action.intent_note ?? '';
  input.addEventListener('input',()=>designUI.notes.set(action.group_key,input.value));
  const row=node('div','decision-actions'),save=node('button','secondary','Use this context & rethink');save.type='submit';save.dataset.designAction='intent';
  const remove=node('button','text-button','Clear explanation');remove.type='button';remove.dataset.designAction='clear';remove.hidden=!action.intent_note;
  remove.addEventListener('click',()=>rethink({intention:{key:action.group_key,note:''}}));
  row.append(save,remove);form.append(label,input,row,node('p','field-help','Your explanation can change the recommendation. A documented requirement still needs to be addressed.'));
  form.addEventListener('submit',e=>{e.preventDefault();if(!input.value.trim()){showError(new Error('Add an explanation, or use Clear explanation.'));return;}rethink({intention:{key:action.group_key,note:input.value}});});
  return form;
}

function renderChangePlan() {
  const p=app.project;if(!p?.action_plan)return;
  const filter=$('plan-filter').value,groups=new Map(p.review.groups.map(g=>[g.id,g])),all=p.action_plan.actions;
  const active=all.filter(a=>!a.reference_only && ['fix','review'].includes(groups.get(a.group_id)?.priority));
  const intentional=active.filter(a=>a.disposition==='intentional').length;
  const noChange=active.filter(a=>a.disposition==='no_change').length;
  $('plan-status').textContent=`${active.length-intentional-noChange} changes or investigations · ${noChange} no change indicated · ${intentional} explained as intentional · ${p.action_plan.status==='ai'?'Vector reconsidered the current context':'Local guidance; Vector has not reconsidered this context'}`;
  const actions=all.filter(a=>filter==='all' ? true : filter==='schematic' ? groups.get(a.group_id)?.schematic_check : filter==='intentional' ? a.disposition==='intentional' : active.includes(a) && !['intentional','no_change'].includes(a.disposition));
  const cards=actions.map((action,index)=>{
    const card=node('details','change-card');card.id='action-'+action.group_id;card.open=action.group_key===designUI.focusKey || !designUI.focusKey && index===0;
    const summary=node('summary'),title=node('div'),kind={change:'Change',investigate:'Check first',intentional:'Intentional — verify',no_change:'No change indicated'}[action.disposition];
    const tags=node('div','badges');tags.append(badge(action.group_id),badge(kind,action.disposition==='change'?'fix':'review'),badge(action.basis));
    title.append(tags,node('h3','',action.title));summary.append(title);card.append(summary);
    const body=node('div','change-body');body.append(node('h4','','What this means'),node('p','',explainIssueText(action.reason)));
    body.append(consequenceCard(action.consequence));
    if(action.context_response){const response=node('div','context-response');response.append(node('strong','','How your context changes this'),node('p','',action.context_response));if(action.intent_note)response.append(node('p','your-intent','Your explanation: '+action.intent_note));body.append(response);}
    body.append(node('h4','','What to do'));
    if(action.disposition==='no_change')body.append(node('p','','No required edit for this finding.'));
    const steps=node('ol','change-steps');for(const [stepIndex,step] of action.steps.entries()){
      const item=node('li');item.append(node('span','',reviewInstruction(step)));
      const ask=node('button','step-ask','Ask about this step');ask.type='button';
      ask.addEventListener('click',()=>openStepChat(action.group_id,stepIndex));item.append(ask);steps.append(item);
    }body.append(steps);
    for(const citation of action.citations || []){
      const source=(p.datasheets.excerpts || []).find(s=>s.evidence_id===citation.evidence_id) || (citation.url ? citation : null);if(!source)continue;
      const quote=node('blockquote','datasheet-quote',citation.quote);sourceLinks(quote,[{url:source.url,title:`${source.mpn} · PDF page ${source.page}`}]);body.append(quote);
    }
    const evidence=node('details','action-evidence');evidence.append(node('summary','','Source evidence & original finding'));
    const ids=node('div','chips');for(const id of action.evidence_ids.filter(id=>!id.startsWith('D:')))ids.append(evidenceLink(id));evidence.append(ids);
    const group=groups.get(action.group_id);if(group){list(evidence,group.evidence || []);sourceLinks(evidence,group.sources);}
    body.append(evidence,intentEditor(action));card.append(body);return card;
  });
  $('change-plan').replaceChildren(...(cards.length?cards:[note(filter==='intentional'?'No items have an intentional explanation yet.':'No active changes in this view. Use All checks to inspect the remaining items.')]));
  renderStepChat();
}

function openIssueChat(groupId) {
  navigate('workspace');
  const section=app.project.review_sections?.assignments?.[groupId]?.section || 'overview';
  selectTab(section);openOverviewIssue(groupId);
  designUI.chatGroup=groupId;designUI.chatStep=0;renderStepChat();
  $('step-chat')?.scrollIntoView({behavior:'smooth',block:'center'});
  $('step-chat-question')?.focus();
}

function savedIssueMessages(groupId) {
  const group=app.project?.review?.groups?.find(g=>g.id===groupId);
  const rows=(app.project?.workspace?.conversation_history || []).filter(entry=>
    ((entry.report_id===app.snapshot.report_id && entry.channel==='issue:'+groupId) || (group?.key && entry.channel==='issue-key:'+group.key)));
  return rows.flatMap(entry=>[{role:'user',text:entry.question},...(entry.answer?[{
    role:'assistant',text:entry.answer,failed:entry.status==='failed',archived:true,question:entry.question,...entry.metadata,proposals:entry.report_id===app.snapshot.report_id?entry.metadata?.proposals:[],evidence_state:entry.metadata?.evidence_state}]:[])]);
}

function stepExplanationRequest(){
  const profile=app.project?.explanation_profile || {};
  const style=profile.mode==='technical'?'Use precise engineering terms; skip basic definitions.':'Use everyday language: explain what this means for the product before the action.';
  const depth=profile.detail==='detailed'?'Explain the reasoning and procedure step by step.':'Keep the answer short.';
  return `Explain this step and what to do next, preserving its limits and uncertainty. ${style} ${depth}`;
}
function openStepChat(groupId,stepIndex=0,prompt=null) {
  prompt=prompt || stepExplanationRequest();
  selectTab(app.project.review_sections?.assignments?.[groupId]?.section==='manufacturing'?'manufacturing':'overview');
  if(overviewUI.focus!==groupId)openOverviewIssue(groupId);
  designUI.chatGroup=groupId;designUI.chatStep=stepIndex;designUI.chatMessages=[];renderStepChat();
  if($('issue-help'))$('issue-help').open=true;
  $('step-chat').scrollIntoView({behavior:'smooth',block:'center'});
  const messages=designUI.chats.get(chatConversationKey(groupId,stepIndex)) || [];
  if(messages.some(message=>message.role==='assistant' && message.question===prompt && message.stepIndex===stepIndex && !message.failed))return;
  return sendStepHelp({preventDefault(){}},prompt);
}

// General conversation is independent of issue/step help.
const generalChat={conversations:new Map()};
function generalConversation(){
  const scope=app.project?.workspace?.project_key || chatReviewScope();
  if(!generalChat.conversations.has(scope)){
    const messages=[];
    for(const entry of app.project?.workspace?.chat_history || []){
      messages.push({role:'user',text:entry.question});
      if(entry.answer)messages.push({role:'assistant',text:entry.answer,failed:entry.status==='failed',question:entry.question,history:[],...entry.metadata});
    }
    generalChat.conversations.set(scope,{messages,draft:'',busy:false,webSearch:true});
  }
  return generalChat.conversations.get(scope);
}
function openChatHome(){navigate('chat');renderChatHome();$('general-chat-question')?.focus();}
function appendChatDecision(parent,message,confirm){
  if(message.role!=='assistant')return;
  if(message.decision_update){
    const update=message.decision_update,box=node('div','review-notice');
    box.append(node('strong','',update.message));
    if(update.next_action)box.append(node('p','',update.next_action));
    if(update.status==='saved' && update.files_changed===false)box.append(node('small','','Project context saved; KiCad files unchanged.'));
    parent.append(box);
  }
  if(confirm && !message.failed){
    for(const proposal of message.proposals || []){
      const button=node('button','secondary',proposal.kind==='part'?proposal.label:'Use this answer');
      button.type='button';button.addEventListener('click',()=>confirm('Confirm: '+proposal.label));parent.append(button);
    }
  }
}
function appendEvidenceWarning(parent,message){
  if(message.role==='assistant' && message.evidence_state?.warning){
    const warning=node('p','review-notice',message.evidence_state.warning);
    warning.setAttribute('role','note');parent.append(warning);
    if(message.evidence_state.status==='unavailable' && app.snapshot.report_id){
      const connect=node('button','secondary','Connect project folder…');connect.type='button';
      connect.title='Choose the KiCad folder again so answers can verify the current files.';
      connect.addEventListener('click',async()=>{
        try{
          const folder=await api('/api/choose-local-folder',{});
          if(folder.cancelled)return;
          await api('/api/fix-design/reconnect',{report_id:app.snapshot.report_id,folder:folder.path});
          await refresh();showError('');
        }catch(error){showError(error);}
      });
      parent.append(connect);
    }
  }
}
function appendChatText(parent,text,seen=null){
  // Render only simple inline formatting; never interpret HTML from the model.
  const pattern=/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|\*\*([^*\n]+)\*\*/g;let cursor=0;
  const enrich=segment=>typeof withComponentContext==='function'?withComponentContext(segment,'full',seen):segment;
  for(const match of text.matchAll(pattern)){
    parent.append(node('span','',enrich(text.slice(cursor,match.index))));
    if(match[3])parent.append(node('strong','',enrich(match[3])));
    else {const link=node('a','chat-source',match[1]);link.href=match[2];link.target='_blank';link.rel='noopener noreferrer';parent.append(link);}
    cursor=match.index+match[0].length;
  }
  parent.append(node('span','',enrich(text.slice(cursor))));
}
function chatAnswerContent(message){
  const paragraph=node('p'),chars=Array.from(message.text || '');let cursor=0;const seen=new Set();
  for(const citation of [...(message.citations || [])].sort((a,b)=>a.start-b.start)){
    if(!Number.isInteger(citation.start) || !Number.isInteger(citation.end) || citation.start<cursor || citation.end<citation.start || citation.end>chars.length || !/^https?:\/\//i.test(citation.url || ''))continue;
    appendChatText(paragraph,chars.slice(cursor,citation.start).join(''),seen);
    const link=node('a','chat-source',`[${citation.title || 'Source'}]`);
    link.href=citation.url;link.target='_blank';link.rel='noopener noreferrer';paragraph.append(link);cursor=citation.end;
  }
  appendChatText(paragraph,chars.slice(cursor).join(''),seen);return paragraph;
}
function renderChatHome(){
  const home=$('chat-home');if(!home)return;
  const state=generalConversation();home.replaceChildren();
  const header=node('div','general-chat-heading');header.append(node('h1','','Vector chat'));
  const fresh=node('button','secondary','Clear chat view');fresh.type='button';fresh.disabled=state.busy;
  fresh.addEventListener('click',()=>{if(state.busy)return;state.messages=[];state.draft='';renderChatHome();});header.append(fresh);home.append(header);
  const project=app.project;
  home.append(node('p','field-help',project?`Vector helps with ${project.provenance?.project_name || project.archive}. It searches saved connections, issues and datasheets. For one finding, open its Issue chat.`:'Ask Vector a question. Open a project for help with your board.'));
  // Web search uses Meta search grounding; answers without provider search proof are labelled as ungrounded.
  const searchLabel=node('label','field-help'),searchToggle=node('input');searchToggle.type='checkbox';searchToggle.id='chat-web-search';searchToggle.checked=false;
  searchLabel.append(searchToggle,node('span','',' Search web when useful'));home.append(searchLabel,node('p','field-help','Searches the web through Vector with cited sources. Project search always runs.'));
  const messages=node('div','general-chat-messages');messages.setAttribute('role','log');messages.setAttribute('aria-live','polite');
  if(!state.messages.length){const welcome=node('div','general-chat-welcome');welcome.append(node('h2','','What would you like to know?'),node('p','','Ask a question, explore an idea, or tell me what you’re trying to do.'));
    const suggestions=node('div','general-chat-suggestions');for(const text of ['Explain my board in simple words','Help me think through a product idea','How do I get started with KiCad?']){
      const b=node('button','secondary',text);b.type='button';b.addEventListener('click',()=>{state.draft=text;renderChatHome();$('general-chat-question')?.focus();});suggestions.append(b);
    }welcome.append(suggestions);messages.append(welcome);
  }
  for(const message of state.messages){const bubble=node('div','step-chat-bubble '+message.role);bubble.append(node('small','',message.role==='user'?'You':message.failed?'Could not answer':message.web_searched?'Vector · Web searched':'Vector'));
    appendEvidenceWarning(bubble,message);bubble.append(chatAnswerContent(message));
    appendChatDecision(bubble,message,!state.busy && message===state.messages.at(-1)?question=>sendGeneralChat(question):null);
    if(message.failed){const retry=node('button','secondary','Retry');retry.type='button';retry.disabled=state.busy;retry.addEventListener('click',()=>sendGeneralChat(message.question,message));bubble.append(retry);}messages.append(bubble);
  }
  if(state.busy)messages.append(node('p','chat-thinking','Thinking…'));
  home.append(messages);
  const form=node('form','general-chat-form'),input=node('textarea');input.id='general-chat-question';input.rows=3;input.maxLength=4000;input.placeholder='Ask Vector about your board…';input.setAttribute('aria-label','Message Vector');input.value=state.draft;input.disabled=state.busy;input.required=true;
  input.addEventListener('input',()=>state.draft=input.value);
  input.addEventListener('keydown',event=>{if(event.key==='Enter' && !event.shiftKey && !event.isComposing){event.preventDefault();if(!state.busy && input.value.trim())sendGeneralChat(input.value.trim());}});
  const send=node('button','primary',state.busy?'Thinking…':'Send');send.type='submit';send.disabled=state.busy;
  form.append(input,send);form.addEventListener('submit',event=>{event.preventDefault();sendGeneralChat(input.value.trim());});home.append(form,node('p','field-help','Enter to send · Shift + Enter for a new line. Confirmed choices are saved and rechecked. File edits still use the approval preview.'));
  if(!app.config.key_configured){const connect=node('button','text-button','Connect Vector in settings');connect.type='button';connect.addEventListener('click',openSettings);home.append(connect);}
  if(state.messages.length)messages.scrollTop=messages.scrollHeight;
}
async function sendGeneralChat(question,retry=null){
  const state=generalConversation(),scope=chatReviewScope(),reportId=app.snapshot?.report_id || null;
  if(state.busy || !question.trim())return;
  if(!app.config.key_configured){state.draft=question;openSettings();return;}
  const history=retry?retry.history:state.messages.filter(m=>!m.failed).slice(-6).map(m=>({role:m.role,text:m.text.slice(0,4000)}));
  const webSearch=retry?retry.webSearch===true:$('chat-web-search')?.checked===true;
  if(retry){const index=state.messages.indexOf(retry);if(index>=0)state.messages.splice(index,1);}else state.messages.push({role:'user',text:question});
  state.busy=true;state.draft='';renderChatHome();
  try{
    const result=await api('/api/chat',{report_id:reportId,question,history,web_search:webSearch});
    if(app.snapshot.report_id===reportId && result.explanation_profile)applyExplanationProfile(result.explanation_profile);
    state.messages.push({role:'assistant',text:result.status==='failed'?result.message:result.answer,failed:result.status==='failed',question,history,webSearch,citations:result.citations || [],web_searched:result.web_searched,evidence_state:result.evidence_state,proposals:result.proposals,decision_update:result.decision_update});
    if(result.status==='failed')state.draft=question;
    if(result.decision_update && app.snapshot.report_id===reportId)await refresh();
  }catch(error){state.messages.push({role:'assistant',failed:true,text:error.message,question,history,webSearch});state.draft=question;}
  finally{state.busy=false;if(chatReviewScope()===scope)renderChatHome();}
}

function renderStepChat() {
  const box=$('step-chat'),p=app.project;if(!box || !p)return;
  const reviewScope=chatReviewScope();
  if(designUI.chatScope!==reviewScope){
    designUI.chatScope=reviewScope;designUI.chatReport=app.snapshot.report_id;designUI.chatGroup=overviewUI.focus;designUI.chatStep=0;designUI.chatMessages=[];designUI.chatDrafts.clear();designUI.chats.clear();
  }
  const actions=p.action_plan?.actions || [];
  if(!actions.length){box.replaceChildren(node('h2','','Issue chat'),node('p','','No issue is selected. Use Vector chat for general questions.'));return;}
  if(!actions.some(a=>a.group_id===designUI.chatGroup))designUI.chatGroup=actions[0].group_id;
  const action=actions.find(a=>a.group_id===designUI.chatGroup);
  if(designUI.chatStep>=action.steps.length)designUI.chatStep=0;
  const conversationKey=chatConversationKey(designUI.chatGroup,designUI.chatStep);
  if(!designUI.chats.has(conversationKey))designUI.chats.set(conversationKey,savedIssueMessages(designUI.chatGroup));
  designUI.chatMessages=designUI.chats.get(conversationKey);
  box.replaceChildren();
  const head=node('div','step-chat-head');head.append(node('h2','','Issue chat · '+designUI.chatGroup),
    node('p','',app.config.key_configured?'Ask Vector about this issue. It uses the saved connections, datasheets and your answers. For other questions, use Vector chat.':'Connect Vector in Review settings, or use the written step guide.'));
  if(!app.config.key_configured){const connect=node('button','text-button','Connect Vector for follow-up questions');connect.type='button';connect.addEventListener('click',openSettings);head.append(connect);}
  head.append(node('p','',action.title));
  const overall=node('button','text-button','Open Vector chat');overall.type='button';overall.addEventListener('click',openChatHome);head.append(overall);
  box.append(head);
  const selects=node('div','step-chat-selects'),issue=node('select'),step=node('select');
  issue.id='step-chat-issue';issue.setAttribute('aria-label','Finding to ask about');
  for(const a of actions){
    const option=node('option','',`${a.group_id} · ${a.title}`);option.value=a.group_id;issue.append(option);
  }
  issue.value=designUI.chatGroup;
  issue.addEventListener('change',()=>{if(app.tab==='chat'){designUI.chatGroup=issue.value;designUI.chatStep=0;renderStepChat();}else{selectTab('overview');openOverviewIssue(issue.value);}});
  step.id='step-chat-step';step.setAttribute('aria-label','Step to ask about');
  for(const [index,entry] of action.steps.entries()){
    const option=node('option','',`Step ${index+1}: ${reviewInstruction(entry).slice(0,80)}${entry.length>80?'…':''}`);option.title=reviewInstruction(entry);option.value=String(index);step.append(option);
  }
  if(!action.steps.length){const option=node('option','','About this finding');option.value='0';step.append(option);}
  step.value=String(designUI.chatStep);
  step.addEventListener('change',()=>{designUI.chatStep=Number(step.value);designUI.chatMessages=[];renderStepChat();});
  if(app.tab==='chat'){const label=node('label','','Discussing');label.htmlFor=issue.id;box.append(label,issue);selects.append(step);}else selects.append(issue,step);
  const context=node('details','chat-context');context.append(node('summary','',app.tab==='chat'?'Choose a specific step':'Choose a different issue or step'),selects);box.append(context);
  const messages=node('div','step-chat-messages');messages.setAttribute('aria-live','polite');
  for(const message of designUI.chatMessages){const bubble=node(message.failed && message.archived?'details':'div',`step-chat-bubble ${message.role}`);
    if(message.failed && message.archived)bubble.append(node('summary','','Earlier request failed · view details'));
    bubble.append(node('small','',message.failed?'Request failed':message.role==='user'?'You':message.kind==='local'?'Built-in guide':'Vector'));
    appendEvidenceWarning(bubble,message);bubble.append(node('p','',message.text));
    appendChatDecision(bubble,message,!designUI.chatBusy && message===designUI.chatMessages.at(-1)?question=>sendStepHelp({preventDefault(){}},question):null);
    if(message.failed && message.question){
      const retry=node('button','secondary','Retry question');retry.type='button';retry.disabled=designUI.chatBusy;
      retry.addEventListener('click',event=>sendStepHelp(event,message.question));bubble.append(retry);
      if(message.detail){const details=node('details');details.append(node('summary','','Technical details'),node('p','',message.detail));bubble.append(details);}
    }
    messages.append(bubble);}
  box.append(messages);
  const form=node('form','step-chat-form'),input=node('textarea');input.id='step-chat-question';input.rows=2;input.maxLength=1000;
  const draftKey=designUI.chatGroup+':'+designUI.chatStep;
  input.value=designUI.chatDrafts.get(draftKey) || '';
  input.addEventListener('input',()=>designUI.chatDrafts.set(draftKey,input.value));
  input.placeholder='What is wrong here? Can you fix it? What do you need to know?' ;input.setAttribute('aria-label','Question about this issue');input.required=app.config.key_configured;input.disabled=designUI.chatBusy || !app.config.key_configured;
  const send=node('button','primary',designUI.chatBusy?'Answering…':app.config.key_configured?'Send question':'Explain selected step');
  send.type='submit';send.dataset.designAction='chat';send.disabled=designUI.chatBusy || !app.snapshot.report_id;
  const footer=node('div','step-chat-footer');footer.append(node('span','',app.config.key_configured?'Vector interpretation · verify before editing':'Written step guide'),send);
  form.append(input,footer);form.addEventListener('submit',sendStepHelp);box.append(form);
  if(app.snapshot.job?.status==='running')box.append(node('p','field-help','You can ask while the review runs. Answers use the saved review currently shown.'));
}

async function sendStepHelp(event, prompt=null) {
  event.preventDefault();if(designUI.chatBusy || !app.snapshot.report_id)return;
  const input=$('step-chat-question'),question=prompt || (app.config.key_configured?input.value.trim():'Explain this step');if(!question)return;
  const reportId=app.snapshot.report_id,reviewScope=chatReviewScope(),groupId=designUI.chatGroup,stepIndex=designUI.chatStep;
  const conversationKey=chatConversationKey(groupId,stepIndex);
  if(!designUI.chats.has(conversationKey))designUI.chats.set(conversationKey,designUI.chatMessages);
  const messages=designUI.chatMessages;
  const history=designUI.chatMessages.filter(m=>!m.failed).slice(-6).map(m=>({role:m.role,text:m.text.slice(0,4000)}));
  designUI.chatDrafts.delete(groupId+':'+stepIndex);
  if(app.config.key_configured)designUI.chatMessages.push({role:'user',text:question});
  else messages.length=0;
  designUI.chatBusy=true;renderStepChat();
  try{
    const result=await api('/api/step-help',{report_id:reportId,group_id:groupId,step_index:stepIndex,question,history});
    if(app.snapshot.report_id!==reportId || chatReviewScope()!==reviewScope)return;
    if(result.explanation_profile)applyExplanationProfile(result.explanation_profile);
    messages.push({role:'assistant',kind:result.status==='local'?'local':'ai',
      failed:result.status==='failed',question,stepIndex,detail:result.detail,provider_label:result.provider_label,evidence_state:result.evidence_state,proposals:result.proposals,decision_update:result.decision_update,
      text:result.status==='failed'?result.message:result.answer});
    if(result.status==='failed')designUI.chatDrafts.set(groupId+':'+stepIndex,question);
    if(result.decision_update)await refresh();
  }catch(error){if(app.snapshot.report_id===reportId && chatReviewScope()===reviewScope){messages.push({role:'assistant',failed:true,question,text:error.message});designUI.chatDrafts.set(groupId+':'+stepIndex,question);}}
  finally{designUI.chatBusy=false;renderStepChat();}
}

function renderAssemblyDownloads() {
  const data=app.project.assembly_export,box=$('assembly-downloads');box.replaceChildren();
  const heading=node('div');heading.append(node('h2','','BOM & placement files'));
  if(!data?.counts){heading.append(node('p','field-help','Run a check or update the plan to generate these files automatically.'));box.append(heading);return;}
  const c=data.counts;heading.append(node('p','',`${c.assigned}/${c.fitted} parts assigned an LCSC code · ${c.bom_lines} BOM lines · ${c.placed} placements`));
  const info=node('p','field-help',`${c.provisional} automatic selections to verify · ${c.unresolved} missing part numbers${c.missing_placements?' · '+c.missing_placements+' missing placements':''}`);heading.append(info);box.append(heading);
  if(data.blockers?.length){list(box,data.blockers);return;}
  const links=node('div','assembly-export-links');
  for(const [file,label,style] of [['assembly-draft.zip','Download BOM + CPL','button primary'],['bom-draft.csv','BOM CSV','button secondary'],['cpl-draft.csv','CPL CSV','button secondary'],['sourcing-review.csv','Selections to review','text-link']]){
    if(file==='cpl-draft.csv' && data.placement.status!=='generated')continue;
    const a=node('a',style,label);a.href=`/reports/${encodeURIComponent(app.snapshot.report_id)}/${file}`;a.download=file;a.dataset.assemblyDownload='true';links.append(a);
  }
  box.append(links,node('p','field-help','Drafts generated from this PCB. Review suggested parts and check placement rotations in your assembler’s preview before ordering.'));
  if(data.placement.status!=='generated')box.append(note(data.placement.message));
}

function partSelectionLink(reference) {
  const button=node('button','chip',reference);button.type='button';
  button.addEventListener('click',()=>{
    selectTab('datasheets');$('datasheet-reference').value=reference;
    const card=[...document.querySelectorAll('.datasheet-card')].find(card=>JSON.parse(card.dataset.references).includes(reference));
    if(card){card.open=true;card.scrollIntoView({behavior:'smooth',block:'center'});}
    else $('datasheet-reference').focus();
  });return button;
}

function renderDatasheets() {
  const p=app.project, data=p.datasheets || {}, box=$('datasheet-list');box.replaceChildren();
  const intro=node('div','datasheet-intro');intro.append(node('h2','','LCSC parts & datasheets'),node('p','',`${data.assigned || 0} parts assigned · ${data.ready || 0} with readable datasheets. Automatic candidates are already included in the draft BOM.`));
  const fetchButton=node('button','secondary','Refresh parts, datasheets & BOM');fetchButton.type='button';fetchButton.dataset.designAction='fetch';fetchButton.addEventListener('click',()=>rethink({datasheets:true}));intro.append(fetchButton);box.append(intro);
  const add=node('form','manual-part-form'),refLabel=node('label','','Component'),refs=node('select');refs.id='datasheet-reference';refLabel.htmlFor=refs.id;
  for(const ref of Object.keys(p.components).sort(natural)){const opt=node('option','',ref);opt.value=ref;refs.append(opt);}
  const codeLabel=node('label','','Exact LCSC code'),code=node('input');code.id='datasheet-code';codeLabel.htmlFor=code.id;code.placeholder='C150772';code.maxLength=13;code.pattern='[Cc][1-9][0-9]{1,11}';code.required=true;
  const save=node('button','secondary','Use this part');save.type='submit';save.dataset.designAction='part';add.append(refLabel,refs,codeLabel,code,save);
  add.addEventListener('submit',e=>{e.preventDefault();rethink({datasheets:true,part_choice:{reference:refs.value,code:code.value.trim().toUpperCase()}});});box.append(add);
  const grouped=new Map();
  for(const part of data.parts || []){
    const key=JSON.stringify([part.query,part.footprint,part.code,part.status,part.candidates,part.message]);
    if(!grouped.has(key))grouped.set(key,{...part,references:[]});grouped.get(key).references.push(part.reference);
  }
  const groups=[...grouped.values()].sort((a,b)=>(!!a.code)-(!!b.code) || natural(a.references[0],b.references[0]));
  for(const part of groups){
    const card=node('details','panel datasheet-card'),head=node('summary','design-heading');head.append(node('h3','',`${part.references.join(', ')} · ${part.query}`),badge(part.code?part.status==='provisional'?'Auto-selected · review':'Part assigned':part.status.replaceAll('_',' '),part.status==='ready'?'completed':'warning'));card.append(head);
    card.dataset.references=JSON.stringify(part.references);
    const selection=node('p','part-selection',part.code?`${part.code} · ${part.mpn || ''} · ${part.package || ''}`:part.message || 'Exact part needed');card.append(selection);
    const doc=data.documents?.[part.code];
    if(doc){card.append(node('p','',`${doc.manufacturer} · ${doc.mpn} · ${doc.package}`));sourceLinks(card,[{url:doc.product_url,title:doc.code+' on LCSC'},{url:doc.pdf_url,title:`Datasheet · ${doc.page_count} pages`}]);card.append(node('p','field-help',`${doc.cached?'Cached download':'Downloaded'} ${new Date(doc.retrieved).toLocaleString()} · ${(part.match || '').replaceAll('_',' ')}${doc.partial?' · Text extraction is partial':''}`));}
    else if(part.product_url)sourceLinks(card,[{url:part.product_url,title:part.code+' on LCSC'},...(part.pdf_url?[{url:part.pdf_url,title:'Open original datasheet'}]:[])]);
    if(part.pdf_message)card.append(node('p','field-help',part.pdf_message));
    if(part.message)card.append(node('p','',part.message));
    if(part.parameters && Object.keys(part.parameters).length)card.append(table(['Listed specification','Value'],Object.entries(part.parameters)));
    if(part.code && part.status==='provisional'){
      const confirm=node('button','secondary',`Confirm ${part.code} for these ${part.references.length} part(s)`);confirm.type='button';confirm.dataset.designAction='confirm';confirm.addEventListener('click',()=>rethink({datasheets:true,part_choice:{reference:part.reference,references:part.references,code:part.code}}));card.append(confirm);
    }
    const alternatives=node('details','part-alternatives');alternatives.append(node('summary','','Other candidates'));
    for(const candidate of part.candidates || []){
      if(candidate.code===part.code)continue;
      const row=node('div','candidate');const label=node('div');label.append(node('strong','',`${candidate.mpn} · ${candidate.package}`),node('p','field-help',candidate.manufacturer));sourceLinks(label,[{url:candidate.product_url,title:candidate.code}]);row.append(label);
      const b=node('button','secondary',`Use ${candidate.code}`);b.type='button';b.dataset.designAction='candidate';b.addEventListener('click',()=>rethink({datasheets:true,part_choice:{reference:part.reference,references:part.references,code:candidate.code}}));row.append(b);alternatives.append(row);
    }
    if(alternatives.children.length>1)card.append(alternatives);
    box.append(card);
  }
  if(!data.parts?.length)box.append(note('Each check automatically searches LCSC and downloads the selected datasheets. Use Refresh to add them to an older run.'));
}

function initializeDesign() {
  const tabs=document.querySelector('[role=tablist]');
  for(const [name,label] of [['datasheets','Parts & datasheets'],['actions','Fix plan']]){
    const b=node('button','',label);b.id='tab-'+name;b.dataset.tab=name;b.setAttribute('role','tab');b.setAttribute('aria-controls','panel-'+name);tabs.prepend(b);
  }
  $('tab-findings').childNodes[0].textContent='Check evidence ';
  $('plan-filter').addEventListener('change',renderChangePlan);
  const label=node('label','lookup-toggle run-lookup'),check=node('input');check.id='run-datasheets';check.type='checkbox';check.checked=true;
  label.append(check,node('span','','Automatically find LCSC parts & datasheets for the BOM'));document.querySelector('.simple-project').append(label);
}


function renderSchematicStatus() {
  const box=$('schematic-status'),p=app.project;box.replaceChildren();if(!p)return;
  const checks=p.circuit?.schematic_checks || [], refs=[...new Set(checks.map(c=>c.reference))];
  const concerns=(p.review?.groups || []).filter(g=>g.schematic_check);
  box.append(node('h3','','Schematic wiring'),node('p','',checks.length?`${checks.length} datasheet checks ran on ${refs.join(', ')}. ${concerns.length} new wiring warnings. Connections needing a closer look: ${checks.filter(c=>['candidate_found','unavailable'].includes(c.status)).length}.`:'Run a new check with KiCad enabled to check schematic pin connections against the supported part requirements.'));
  const ai=p.ai_review?.status==='completed' && Array.isArray(p.ai_review?.result?.circuit_findings);
  box.append(node('p','field-help',ai?'Vector also reviewed the supplied datasheet excerpts and your device description. Its suggestions need your review.':'Enable Vector review for additional schematic concerns based on the downloaded datasheets and your device description.'));
  box.append(node('p','field-help','These checks do not establish real supply voltages, firmware settings, or every component requirement. Add those details to “What this device is meant to do”.'));
  if(concerns.length){const b=node('button','secondary','See wiring checks');b.type='button';b.addEventListener('click',()=>{$('plan-filter').value='schematic';renderChangePlan();selectTab('actions');$('panel-actions').scrollIntoView({behavior:'smooth'});});box.append(b);}
  if(checks.length){const detail=node('details');detail.append(node('summary','','What was checked'));for(const check of checks)detail.append(node('p','field-help',`${check.reference} · ${check.check}: ${check.detail}`));box.append(detail);}
}
