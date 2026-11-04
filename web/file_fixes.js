// A checked, user-requested fix prepares and validates a copy before writing it.
const FILE_FIX_BEFORE='Save your work in KiCad, then close this board’s PCB Editor window. You can leave the project manager and schematic open.';
const FILE_FIX_AFTER='When it finishes, reopen the same .kicad_pcb file in KiCad to see the change. Then update the review here.';
const FIX_INVESTIGATION_QUESTION='Work out a fix for this finding using the saved circuit, connected parts, routing paths and datasheet evidence. First decide whether it needs a change. Do the analysis yourself. Name exact parts, pins, nets and supported changes, and how to verify them. If information is missing, recommend a conditional approach and identify the specific facts blocking an edit. Do not invent an antenna, measurements or requirements. This investigation cannot modify files.';
const fixInvestigations=new Map();
const fixDialogs=new Map();

function fixReviewKey(group){
  const project=app.project || {};
  const current=project.review?.groups?.find(item=>item.id===group.id) || group;
  return JSON.stringify([app.snapshot.report_id,group.id,project.provenance?.source_hashes,
    project.workspace?.design_context?.revision,current.signature,current.summary,
    project.action_plan?.actions?.find(action=>action.group_id===group.id)]);
}

function claimFixDialog(key){
  const existing=fixDialogs.get(key);
  if(existing?.isConnected && existing.open!==false){existing.focus?.();return null;}
  const dialog=node('dialog','file-fix-dialog');fixDialogs.set(key,dialog);
  dialog.addEventListener('close',()=>{if(fixDialogs.get(key)===dialog)fixDialogs.delete(key);});
  return dialog;
}

function fixInvestigationPanel(group) {
  const reportId=app.snapshot.report_id,key=fixReviewKey(group),box=node('div','fix-investigation');
  if(!fixInvestigations.has(key))fixInvestigations.set(key,{messages:[],busy:false,started:false,draft:'',error:null,listeners:new Set()});
  const state=fixInvestigations.get(key);
  const assessment=app.project.file_fix_options?.[group.id]?.assessment;
  const current=()=>app.snapshot.report_id===reportId && fixReviewKey(group)===key;
  const notify=()=>{for(const listener of state.listeners)listener();};
  const staleNotice=()=>'The review changed since this investigation started. Close this window and reopen the issue to investigate the latest files.';
  function markStale(question,history){
    state.error={message:staleNotice(),stale:true,question,history:history || []};
    notify();
  }
  async function send(question,retry=false) {
    if(state.busy)return;
    if(!current()){markStale(question,retry && state.error?state.error.history:[]);return;}
    if(!app.config.key_configured){openSettings(()=>{if(box.isConnected && current())send(question,retry);});return;}
    const history=retry?state.error.history:state.messages.filter(m=>!m.failed).slice(-6).map(m=>({role:m.role,text:m.text.slice(0,4000)}));
    const message=retry?state.error.userMessage:{role:'user',text:question,initial:question===FIX_INVESTIGATION_QUESTION};
    if(!retry)state.messages.push(message);
    state.started=true;state.busy=true;state.error=null;state.draft='';notify();
    try {
      const result=await api('/api/step-help',{report_id:reportId,group_id:group.id,step_index:0,question,history});
      if(!current()){message.failed=true;state.started=false;state.error={message:staleNotice(),stale:true,question,history};return;}
      if(result.status==='failed'){
        message.failed=true;state.error={message:result.message,detail:result.detail,question,history,userMessage:message};
      }else{
        message.failed=false;state.messages.push({role:'assistant',text:result.answer,provider_label:result.provider_label});
      }
    }catch(error){
      message.failed=true;
      if(current())state.error={message:error.message,question,history,userMessage:message};
      else{state.started=false;state.error={message:staleNotice(),stale:true,question,history};}
    }finally{state.busy=false;notify();}
  }
  function render() {
    box.replaceChildren();
    if(!current()){box.append(node('p','review-notice','The review changed. Close this window and reopen the issue to investigate the latest files.'));return;}
    for(const message of state.messages){
      if(message.initial)continue;
      const bubble=node('section','fix-investigation-message');
      bubble.append(node('strong','',message.role==='user'?'You':'Vector · repair discussion'),node('p','',message.text));box.append(bubble);
    }
    if(state.busy){
      const progress=node('p','fix-investigation-progress','Investigating this issue…');progress.setAttribute('role','status');
      box.append(progress,node('p','field-help','Reviewing the saved circuit paths, connected parts and available datasheet evidence. Retries can take up to two minutes. You can keep KiCad open.'));
    }
    if(state.error){
      box.append(node('p','review-notice',state.error.message));
      if(!state.error.stale){
        const retry=node('button','secondary','Retry investigation');retry.type='button';
        retry.addEventListener('click',()=>{if(state.error && !state.busy)send(state.error.question,true);});box.append(retry);
      }
      if(state.error.detail){const details=node('details');details.append(node('summary','','Technical details'),node('p','',state.error.detail));box.append(details);}
    }
    if(!state.started && !assessment){
      const start=node('button','primary',app.config.key_configured?'Investigate this issue':'Connect Vector');start.type='button';
      start.addEventListener('click',()=>send(FIX_INVESTIGATION_QUESTION));box.append(start);
    }
    if(assessment || state.messages.some(m=>m.role==='assistant')){
      const form=node('form','fix-investigation-form'),input=node('textarea');input.rows=3;input.maxLength=1000;input.required=true;
      input.setAttribute('aria-label','Follow up about this fix');input.placeholder='Ask about the proposed fix or add a missing detail…';input.value=state.draft;input.disabled=state.busy;
      input.addEventListener('input',()=>{state.draft=input.value;});
      const button=node('button','secondary','Ask follow-up');button.type='submit';button.disabled=state.busy;
      form.append(input,button);form.addEventListener('submit',event=>{event.preventDefault();const question=input.value.trim();if(question)send(question);});box.append(form);
    }
    box.append(node('p','field-help','Investigation only. No project files have been changed.'));
  }
  const listener=()=>{if(box.isConnected)render();};state.listeners.add(listener);
  box.disposeInvestigation=()=>state.listeners.delete(listener);
  box.startInvestigation=()=>{if(!assessment && !state.started && app.config.key_configured)send(FIX_INVESTIGATION_QUESTION);};
  render();return box;
}

function fileFixEntry(group, action=null) {
  action=action || app.project.action_plan?.actions?.find(item=>item.group_id===group.id);
  const option=app.project.file_fix_options?.[group.id];
  if(action?.reference_only || action?.disposition==='no_change' || action?.action_kind==='order_setting')return null;
  const assignment=app.project.review_sections?.assignments?.[group.id];
  if(assignment && (!assignment.attention || assignment.status==='context'))return null;
  const automatic=option?.mode==='automatic';
  const entry=node('div','file-fix-entry'),button=node('button','primary',assignment?.status==='context'?'Assess possible fix':'Fix this issue');button.type='button';
  entry.append(button,node('span','field-help',automatic?
    'Prepare a change → check it → approve the file update':
    'Work out a repair → check the proposed changes → ask for your approval. If blocked, explain what is needed.'));
  button.addEventListener('click',()=>openFixWorkflow(group));
  return entry;
}

function openFixWorkflow(group) {
  if(app.project.action_plan?.actions?.find(item=>item.group_id===group.id)?.action_kind==='order_setting')return;
  const reportId=app.snapshot.report_id,dialog=claimFixDialog('options:'+fixReviewKey(group));
  if(!dialog)return;
  const heading=node('div','dialog-heading'),close=node('button','icon-button','×');close.type='button';
  close.setAttribute('aria-label','Close fix options');close.addEventListener('click',()=>dialog.close());
  heading.append(node('h2','','Fix this issue'),close);
  dialog.append(heading,node('p','',group.title || 'Selected finding'));
  const control=fileFixControl(group,()=>{
    if(app.snapshot.report_id!==reportId){
      dialog.replaceChildren(node('h2','','Review changed'),node('p','','Close this window and select the finding in the latest review.'),close);
      return false;
    }
    dialog.close();return true;
  });dialog.append(control);
  document.body.append(dialog);dialog.addEventListener('close',()=>{control.disposeInvestigation?.();dialog.remove();},{once:true});dialog.showModal();
  // A prepared record already answers the investigation: do not auto-fire a
  // second one for the same finding, or its job slot 409s the Continue action.
  const resumed=(app.project.workspace?.file_fixes || []).some(f=>f.group_id===group.id && ['ready','awaiting_design_approval','design_approved','applied'].includes(f.status));
  if(!resumed)control.startInvestigation?.();
}

function fileFixControl(group, beforeAction=()=>true) {
  const box=node('div','file-fix-control');
  const option=app.project.file_fix_options?.[group.id],automatic=option?.mode==='automatic';
  const proposal=node('form','fix-investigation-form'),request=node('textarea');request.rows=3;request.maxLength=2000;
  request.setAttribute('aria-label','Describe the file changes');request.placeholder='Optional: add constraints or request a specific change. For a footprint replacement, include its Library:Name.';
  const plan=node('button','primary','Prepare my fix');plan.type='submit';
  proposal.append(node('h3','','Prepare changes to your files'),node('p','field-help',automatic?
    'The app prepares the supported change on a copy. It must pass KiCad checks, and you approve it before the project file changes.':
    'The app first checks the circuit, part documentation and board layout. It only offers a file edit if the evidence supports one and the staged KiCad checks pass. Otherwise, it explains what is missing.'),request,plan);
  proposal.addEventListener('submit',event=>{event.preventDefault();const text=request.value.trim() || 'Prepare exact file edits to resolve this finding using the saved circuit and layout evidence. Include any required moves of nearby footprints and routing changes. If a repair is not established, identify the facts needed instead of inventing an edit.';if(app.busy)return;
    // Deterministic findings skip the Meta design investigation entirely:
    // the backend applies the supported edit directly, which is fast and
    // needs no API key. Research stays the default for anything else.
    if(!automatic && !app.config.key_configured){openSettings();return;}
    if(beforeAction())openFileFix(group,null,false,text,!automatic);
  });box.append(proposal);
  const previous=(app.project.workspace?.file_fixes || []).filter(f=>f.group_id===group.id).at(-1);
  if(['ready','awaiting_design_approval','design_approved'].includes(previous?.status)){
    const view=node('button','secondary',previous.status==='ready'?'Review prepared changes':previous.status==='design_approved'?'Prepare approved edits':'Continue repair');view.type='button';
    view.addEventListener('click',()=>{if(beforeAction())openFileFix(group,previous.id);});box.append(view);
  }
  if(previous?.status==='applied'){
    box.append(node('strong','','File updated'),node('p','field-help',FILE_FIX_AFTER));
    const view=node('button','secondary','View fix / Undo');view.type='button';
    view.addEventListener('click',()=>{if(beforeAction())openFileFix(group,previous.id);});box.append(view);return box;
  }
  if(option?.mode!=='automatic'){
    box.append(node('p','field-help',option?.message || 'An automatic file edit is not available for this finding.'));
    if(option?.assessment){
      const assessment=option.assessment;
      const evidence=node('details');evidence.append(node('summary','','Current evidence'),node('h3','','What the saved board shows'));
      const observations=node('ul');for(const text of assessment.observations || [])observations.append(node('li','',text));evidence.append(observations);
      evidence.append(node('p','field-help','These measurements start the investigation. They do not prove the antenna is missing or that the routing needs a change.'));box.append(evidence);
    }
    const investigation=fixInvestigationPanel(group);box.append(investigation);
    box.startInvestigation=investigation.startInvestigation;box.disposeInvestigation=investigation.disposeInvestigation;
    return box;
  }
  box.append(node('p','',FILE_FIX_BEFORE));
  const label=node('label','check'),closed=node('input');closed.type='checkbox';
  label.append(closed,document.createTextNode(' I saved and closed the PCB Editor for this board.'));box.append(label);
  const button=node('button','primary','Apply fix');button.type='button';button.disabled=true;
  closed.addEventListener('change',()=>{button.disabled=!closed.checked || app.busy;});
  button.addEventListener('click',()=>{if(closed.checked && !app.busy && beforeAction())openFileFix(group,previous?.id,true);});box.append(button);
  box.append(node('p','field-help','Checks the edit, saves a backup, then fixes the file. Keep the PCB Editor closed until it finishes.'));
  box.append(node('p','field-help',FILE_FIX_AFTER));
  return box;
}

async function openFileFix(group, existingId=null, applyRequested=false, editRequest=null, investigateDesign=false, designId=null) {
  const reportId=app.snapshot.report_id;
  const dialog=claimFixDialog('edit:'+fixReviewKey(group)+':'+(existingId || 'new'));
  if(!dialog)return;
  const heading=node('div','dialog-heading'),title=node('h2','',applyRequested?'Fixing this…':'File fix'),close=node('button','icon-button','×');
  close.type='button';close.setAttribute('aria-label','Close file fix');close.addEventListener('click',()=>dialog.close());
  heading.append(title,close);const body=node('div');dialog.append(heading,body);document.body.append(dialog);
  dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.showModal();
  body.append(node('p','',editRequest?'Planning and checking a copy. Your files are unchanged; you can keep KiCad open.':'Checking the edit on a copy. Keep this board’s PCB Editor closed.'));
  let fix,continuing=false,writing=false;
  async function continueDesign(data) {
    if(continuing || app.snapshot.report_id!==reportId)return;
    continuing=true;
    const request=fix.edit_request || editRequest;
    body.replaceChildren(node('p','','Continuing the repair…'));
    try{
      const next=await api('/api/file-fix/continue',{fix_id:fix.id,...data});
      dialog.close();openFileFix(group,next.id,false,request);
    }catch(error){render();body.append(node('p','review-notice',error.message));}
    finally{continuing=false;}
  }
  async function writeFix(undo=false) {
    if(writing || !dialog.isConnected || app.snapshot.report_id!==reportId)return;
    writing=true;
    title.textContent=undo?'Restoring the original…':'Saving the fix…';
    body.replaceChildren(node('p','','Keep the PCB Editor closed. The file is being updated.'));
    try{
      const result=await api('/api/file-fix/'+(undo?'undo':'apply'),{fix_id:fix.id,closed_in_kicad:true,...(fix.requires_approval?{approve_plan:fix.id}:{})});
      fix={...fix,...result};render();
      if(app.snapshot.report_id===reportId)app.project=await api('/api/project');
    }catch(error){render();body.append(node('p','review-notice',error.message));}
    finally{writing=false;}
  }
  function offerFolderReconnect() {
    const connect=node('button','secondary','Connect folder…');connect.type='button';
    body.append(connect,node('p','field-help','Your files stay unchanged. The folder is accepted only when every analyzed file matches.'));
    connect.addEventListener('click',async()=>{
      try{
        const folder=await api('/api/choose-local-folder',{});
        if(folder.cancelled)return;
        title.textContent='Reconnecting folder…';
        await api('/api/fix-design/reconnect',{report_id:reportId,folder:folder.path});
        const request=fix?.edit_request ?? editRequest;
        dialog.close();openFileFix(group,existingId,applyRequested,request,investigateDesign,designId);
      }catch(retryError){title.textContent='Could not complete the fix';body.replaceChildren(node('p','review-notice',retryError.message));offerFolderReconnect();}
    });
  }
  function confirmationButton(text, undo=false) {
    const label=node('label','check'),check=node('input');check.type='checkbox';
    label.append(check,document.createTextNode(fix.requires_approval?(undo?' I approve restoring the backed-up files and have saved and closed both editors.':' I approve every listed change and have saved and closed both the PCB and schematic editors.'):' I saved and closed the PCB Editor for this board.'));body.append(label);
    const button=node('button',undo?'secondary':'primary',text);button.type='button';button.disabled=true;
    check.addEventListener('change',()=>{button.disabled=!check.checked;});
    button.addEventListener('click',()=>{if(check.checked){button.disabled=true;writeFix(undo);}});body.append(button);
  }
  function render() {
    if(!dialog.isConnected)return;
    editRequest=fix.edit_request || editRequest;
    investigateDesign=fix.investigate_design ?? investigateDesign;
    body.replaceChildren();
    if(fix.status==='awaiting_design_approval'){
      title.textContent='1 · Review the repair approach';
      const design=fix.design;
      const aiAvailable=design.ai_assessment_status==='available';
      if(aiAvailable)body.append(node('p','review-notice','Vector assessment available · safety checks applied'));
      if(design.service_message){
        if(!aiAvailable)body.append(node('p','review-notice','Reference-based proposal · Vector assessment unavailable'));
        const service=node('details');service.append(node('summary','','Service details'),node('p','',design.service_message));body.append(service);
        if(!aiAvailable){
          const retry=node('button','secondary','Retry Vector assessment');retry.type='button';
          retry.addEventListener('click',()=>{const request=fix.edit_request;dialog.close();openFileFix(group,null,false,request,true);});
          body.append(retry);
        }
      }
      const enrich=(text,form='full')=>typeof withComponentContext==='function'?withComponentContext(text,form):text;
      body.append(node('h3','',enrich(design.title,'short')),node('p','',enrich(design.diagnosis)),node('h3','','Proposed approach'),node('p','',enrich(design.approach)));
      const savedAnswers=Object.entries(fix.design_answers || {}).filter(([,value])=>typeof value==='string' && value.trim() && !/^(i\s+(?:just|jsut) told you(?:\s+man)?|already answered|same as before)$/i.test(value.trim()));
      if(savedAnswers.length){
        const labels={external_enclosure_metal:'Enclosure and nearby metal',antenna_environment:'Antenna environment',external_enclosure:'Enclosure',enclosure_material:'Enclosure material',battery_placement:'Battery position',battery_capacity:'Battery choice'};
        body.append(node('h3','','Your saved context'));const list=node('ul');
        for(const [key,value] of savedAnswers)list.append(node('li','',(labels[key] || key.replaceAll('_',' '))+': '+value));
        body.append(list);
      }
      for(const [key,label] of [['changes','Planned changes'],['assumptions','Assumptions'],['validation','Checks after editing'],['limitations','Still needs verification']]){
        if(!design[key]?.length)continue;body.append(node('h3','',label));const list=node('ul');for(const text of design[key])list.append(node('li','',enrich(text)));body.append(list);
      }
      if(design.requirements?.length){
        const sources=node('details');sources.append(node('summary','','Datasheet requirements used'));
        for(const requirement of design.requirements){
          const source=design.sources.find(s=>s.evidence_id===requirement.evidence_id);
          sources.append(node('p','',requirement.requirement),node('blockquote','',requirement.quote));
          if(source){const link=node('a','text-link',(source.title || source.mpn || 'Datasheet')+' · page '+source.page);link.href=source.url;link.target='_blank';link.rel='noopener';sources.append(link);}
        }body.append(sources);
      }
      if(design.retrieval_limits?.length){const limits=node('details');limits.append(node('summary','','Document lookup limits'));for(const text of design.retrieval_limits)limits.append(node('p','',text));body.append(limits);}
      if(design.questions.length){
        body.append(node('h3','','Decisions needed from you'));
        const form=node('form','fix-investigation-form'),inputs=[];
        for(const q of design.questions){const label=node('label','',reviewInstruction(q.question)),input=node('textarea');input.rows=2;input.maxLength=2000;input.required=true;input.placeholder='Your answer, or unknown';input.setAttribute('aria-label',q.question);label.append(input,node('small','field-help',enrich(q.why_needed)));form.append(label);inputs.push([q.id,input]);}
        const next=node('button','primary','Update the design with my answers');next.type='submit';form.append(next);
        form.addEventListener('submit',event=>{event.preventDefault();const answers=Object.fromEntries(inputs.map(([id,input])=>[id,input.value.trim()]));if(Object.values(answers).every(Boolean))continueDesign({answers});});body.append(form);
      }else if(design.preparation_blocker){
        body.append(node('p','review-notice',design.preparation_blocker));
      }else{
        const approve=node('button','primary','Approve approach and prepare edits');approve.type='button';approve.addEventListener('click',()=>continueDesign({approve_design:fix.id}));body.append(approve);
      }
      body.append(node('p','field-help','This approves the design approach only. The exact file changes will need a separate approval. Your project files are unchanged.'));return;
    }
    if(fix.status==='design_approved'){
      title.textContent='2 · Prepare the exact edits';
      body.append(node('h3','',fix.design?.title || 'Approved repair approach'),
        node('p','',fix.design?.approach || fix.summary || ''),
        node('p','field-help','The repair approach is already approved. Next, Vector will propose exact placement and routing changes, and KiCad will check them on a copy. Your project files remain unchanged until you review and approve that preview.'));
      const prepare=node('button','primary','Prepare and check exact edits');prepare.type='button';
      prepare.addEventListener('click',()=>continueDesign({approve_design:fix.id}));body.append(prepare);return;
    }
    if(fix.validation_history?.length){
      const history=node('details');history.append(node('summary','','Repair validation history'));
      for(const event of fix.validation_history){
        if(!['drc_failed','ready_for_approval'].includes(event.stage))continue;
        history.append(node('p','',`Attempt ${event.attempt+1}: ${event.stage==='ready_for_approval'?'No new blocking DRC findings; ready for approval':`${event.new_error_count} new DRC findings; correction requested (${(event.rules || []).join(', ')})`}`));
      }
      body.append(history);
    }
    if(fix.status==='preparing'){
      body.append(node('p','',fix.message),node('p','field-help',editRequest?'No source files are changed during preparation. The preview will need your approval.':'Keep the PCB Editor closed. This edit will be saved only if its checks pass.'));return;
    }
    if(fix.status==='blocked'){
      title.textContent=fix.message?.includes('ran out of storage') || fix.message?.includes('No space left on device') || fix.message?.includes('Errno 28')?'Not enough disk space':/timed out|did not respond before/i.test(fix.message || '')?'Vector timed out':fix.message?.includes('No edit prepared')?'No safe edit was identified':editRequest?'Edit preparation stopped':'Could not fix this automatically';
      body.append(node('p','',fix.message),node('p','field-help','Your project files were not changed. You can reopen the PCB Editor.'));
      if(/connect the .*folder/i.test(fix.message || ''))offerFolderReconnect();
      if(editRequest){
        const retry=node('button','secondary','Retry the same edit');retry.type='button';retry.addEventListener('click',()=>{dialog.close();openFileFix(group,null,false,editRequest,investigateDesign,fix.design_id || designId);});body.append(retry);
        const form=node('form','fix-investigation-form'),input=node('textarea');input.value=editRequest;input.rows=3;input.maxLength=2000;input.required=true;input.setAttribute('aria-label','Revise the edit request');
        const revise=node('button','primary','Revise request and retry');revise.type='submit';form.append(input,revise);
        form.addEventListener('submit',event=>{event.preventDefault();const request=input.value.trim();if(request){dialog.close();openFileFix(group,null,false,request,investigateDesign,fix.design_id || designId);}});body.append(form);
      }return;
    }
    const finished=['applied','undone'].includes(fix.status);
    title.textContent=fix.status==='applied'?'Fixed in your PCB file':fix.status==='undone'?'Original file restored':'Ready to fix';
    body.append(node('h3','',fix.summary));
    if(fix.change_summary){
      body.append(node('h3','',finished?(fix.status==='undone'?'Changes reverted':'Changes made'):'Changes to approve'));
      const changes=node('ol');for(const text of fix.change_summary)changes.append(node('li','',text));body.append(changes);
      for(const warning of fix.warnings || [])body.append(node('p','review-notice',warning));
      const previews=node('details');previews.append(node('summary','','Before / after · both board sides'));
      for(const name of fix.previews || []){const picture=node('img');picture.src='/api/file-fix-preview/'+fix.id+'/'+name;picture.alt=name.replace('.svg','').replace('-',' · ');picture.style.width='100%';previews.append(node('p','',picture.alt),picture);}body.append(previews);
    }
    if(finished){
      body.append(node('h3','','To see the change'));
      const steps=node('ol');steps.append(node('li','',fix.requires_approval?'Reopen the PCB and schematic from the same KiCad project.':'Reopen '+fix.file.split('/').at(-1)+' from the same project in KiCad.'),
        node('li','',fix.status==='undone'?'The original PCB is now back in place.':'The saved edit will appear in the PCB Editor.'),
        node('li','','Click Update review below to check this saved version.'));body.append(steps);
      const update=node('button','primary','Update review');update.type='button';
      update.addEventListener('click',()=>{dialog.close();updateSavedProject();});body.append(update);
    }else{
      body.append(node('p','',fix.requires_approval?'Save and close both the PCB Editor and Schematic Editor before approving. Reopen both after saving to see the changes.':FILE_FIX_BEFORE));confirmationButton(fix.requires_approval?'Approve and apply changes':'Fix this');
    }
    const details=node('details');details.append(node('summary','','What changed & checks'),node('p','',fix.basis),node('p','field-help','File: '+fix.file));
    if(fix.kind==='ground_connection')details.append(node('p','',`${fix.length_mm} mm of ${fix.width_mm} mm copper on ${fix.layer}.`),
      node('p','field-help','DRC checks the ground connection’s geometry; it does not verify ground-return currents, noise or the overall circuit.'));
    const list=node('ul');for(const check of fix.checks || [])list.append(node('li','',check));details.append(list);
    const diff=node('details');diff.append(node('summary','','Exact file changes'),node('pre','file-fix-diff',fix.diff || ''));
    const download=node('a','text-link','Download complete diff');download.href='/api/file-fix-diff/'+fix.id;download.download='pcb-file-change.diff';diff.append(download);details.append(diff);
    details.append(node('p','field-help','Backup: '+fix.backup));body.append(details);
    if(fix.status==='applied'){
      body.append(node('h3','','Undo this fix'),node('p','field-help',fix.requires_approval?'To undo, save and close both editors. Undo stops if an edited file has changed since the fix.':'To undo, save and close this board’s PCB Editor again. Undo will stop if you have made further PCB edits.'));
      confirmationButton('Undo this fix',true);
    }
  }
  try {
    fix=existingId?await api('/api/file-fix/'+existingId):await api('/api/file-fix/prepare',{report_id:reportId,group_id:group.id,...(editRequest?{edit_request:editRequest}:{}),...(investigateDesign?{investigate_design:true}:{}),...(designId?{design_id:designId}:{})});
    while(dialog.isConnected && fix.status==='preparing'){
      render();await new Promise(resolve=>setTimeout(resolve,1000));
      if(!dialog.isConnected)return;
      fix=await api('/api/file-fix/'+fix.id);
    }
    if(!dialog.isConnected)return;
    if(app.snapshot.report_id!==reportId){title.textContent='Review changed';body.replaceChildren(node('p','','No edit was applied. Open the original review and try again.'));return;}
    if(applyRequested && fix.status==='ready' && !fix.requires_approval)await writeFix();else render();
    if(app.snapshot.report_id===reportId)app.project=await api('/api/project');
  }catch(error){
    title.textContent='Could not complete the fix';
    body.replaceChildren(node('p','review-notice',error.message));
    if(/connect the .*folder/i.test(String(error&&error.message||error)))offerFolderReconnect();
  }
}
