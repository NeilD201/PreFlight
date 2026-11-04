// Coordinated Fix Design: plan on an isolated copy, preview, approve/reject,
// export a separate corrected project. The original is never edited.
function coordinatedFixEntry(group, action=null) {
  action=action || app.project.action_plan?.actions?.find(item=>item.group_id===group.id);
  if(group.no_change_basis || group.reference_only || action?.reference_only || action?.disposition==='no_change' || action?.action_kind==='order_setting')return null;
  const assignment=app.project.review_sections?.assignments?.[group.id];
  if(assignment && (!assignment.attention || assignment.status==='context'))return null;
  if(app.project.file_fix_options?.[group.id]?.mode!=='automatic')return null;
  const entry=node('div','file-fix-entry'),button=node('button','secondary','Plan coordinated fix');button.type='button';
  entry.append(button,node('span','field-help','Run every supported fix for this finding as one checked plan on an isolated copy, then approve and export.'));
  button.addEventListener('click',()=>openFixDesign(group));
  const prior=(app.fixDesigns && app.fixDesigns.report_id===(app.snapshot || {}).report_id ? app.fixDesigns.items : []).filter(f=>(f.group_ids || []).includes(group.id));
  if(prior.length){
    const latest=prior[0];
    const resume=node('button','secondary','Resume checked fix ('+latest.status+')');resume.type='button';
    resume.setAttribute('data-resume-fix',latest.id);
    resume.addEventListener('click',()=>openFixDesign(group,latest.id));
    entry.append(resume,node('span','field-help',prior.length>1?prior.length+' previous plans for this finding; showing the newest.':'A previous plan for this finding is saved, including after reload.'));
  }
  return entry;
}

async function openFixDesign(group, existingId=null) {
  const reportId=app.snapshot.report_id;
  const dialog=claimFixDialog('fixdesign:'+group.id+':'+(existingId || 'new'));
  if(!dialog)return;
  const heading=node('div','dialog-heading'),close=node('button','icon-button','×');close.type='button';
  close.setAttribute('aria-label','Close coordinated fix');close.addEventListener('click',()=>dialog.close());
  heading.append(node('h2','','Coordinated fix'),close);
  const body=node('div');dialog.append(heading,body);document.body.append(dialog);
  dialog.addEventListener('close',()=>{polling=false;dialog.remove();},{once:true});dialog.showModal();
  let fix=null,polling=false,working=false;
  function render() {
    if(!dialog.isConnected)return;
    body.replaceChildren();
    body.append(node('p','',group.title || 'Selected finding'));
    if(!fix){body.append(node('p','','Starting…'));return;}
    if(fix.status==='preparing'){
      body.append(node('p','','Preparing the coordinated fix on an isolated copy. Your files are unchanged.'),node('p','field-help',fix.message || ''));
      return;
    }
    if(fix.status==='blocked'){
      body.append(node('p','review-notice',fix.message || 'Preparation failed. Your project files were not changed.'));
      const retry=node('button','secondary','Try again');retry.type='button';
      retry.addEventListener('click',()=>{if(!working)start();});body.append(retry);
      if(/folder/i.test(fix.message || ''))appendFolderRecovery();
      return;
    }
    if(fix.status==='validated' || fix.status==='approved' || fix.status==='exported'){
      body.append(node('h3','','Checked preview'));
      const list=node('ul');
      for(const step of fix.steps || [])list.append(node('li','',(step.group_id || '?')+': '+step.status+(step.kind?' · '+step.kind:'')+(step.attempts>1?' · '+step.attempts+' attempts':'')));
      body.append(list);
      body.append(node('p','','Files changed: '+(fix.files_changed || []).join(', ') || 'none'));
      const checks=[];for(const step of fix.steps || [])for(const text of step.recheck?.checks || [])checks.push(text);
      if(checks.length){body.append(node('h3','','Validation checks'));const cl=node('ul');for(const text of checks.slice(0,12))cl.append(node('li','',text));body.append(cl);}
      const diffs=fix.diffs || {};const names=Object.keys(diffs);
      if(names.length){const details=node('details');details.append(node('summary','','Exact file changes'));for(const name of names)details.append(node('p','',name),node('pre','file-fix-diff',diffs[name]));body.append(details);}
      if(fix.status==='validated'){
        const label=node('label','check'),check=node('input');check.type='checkbox';
        label.append(check,document.createTextNode(' I approve exporting this checked copy as a separate corrected project. The original stays unchanged.'));body.append(label);
        const approve=node('button','primary','Approve and keep for export');approve.type='button';approve.disabled=true;
        check.addEventListener('change',()=>{approve.disabled=!check.checked;});
        approve.addEventListener('click',async()=>{if(!check.checked || working)return;working=true;try{fix=await api('/api/fix-design/approve',{fix_id:fix.id});fix=await api('/api/fix-design/'+fix_id());render();}catch(error){body.append(node('p','review-notice',error.message));}finally{working=false;}});
        body.append(approve);
        const form=node('div');const reason=node('input');reason.placeholder='Rejection reason (optional)';reason.maxLength=500;
        const reject=node('button','secondary','Reject fix');reject.type='button';
        reject.addEventListener('click',async()=>{if(working)return;working=true;try{await api('/api/fix-design/reject',{fix_id:fix.id,reason:reason.value.trim()});fix=await api('/api/fix-design/'+fix_id());render();}catch(error){body.append(node('p','review-notice',error.message));}finally{working=false;}});
        form.append(reason,reject);body.append(form);return;
      }
      const link=node('a','text-link','Download corrected project (.zip)');link.href='/api/fix-design-export/'+fix.id;body.append(link);
      body.append(node('p','field-help','The download is a separate corrected project. Your original folder is unchanged.'));
      return;
    }
    if(fix.status==='rejected'){
      body.append(node('p','','Rejected. The candidate was restored and the original was never changed.'));
      const retry=node('button','secondary','Plan again');retry.type='button';
      retry.addEventListener('click',()=>{if(!working)start();});body.append(retry);return;
    }
    body.append(node('p','review-notice',fix.message || ('Status: '+fix.status)));
  }
  function fix_id(){return fix.id;}
  async function poll() {
    if(!polling || !dialog.isConnected)return;
    try{
      const current=await api('/api/fix-design/'+fix.id);
      fix=current;
      if(fix.status!=='preparing'){polling=false;}
      render();
    }catch(error){
      if(dialog.isConnected)body.append(node('p','review-notice',error.message));
      polling=false;return;
    }
    if(polling)setTimeout(poll,3000);
  }
  // Folder recovery is shared by the blocked-status branch and the
  // synchronous-prepare-failure path: a synchronous raise (e.g. an
  // upload-born review) never produces a fix record, so the catch path must
  // offer the same typed-folder + retry options instead of a dead notice.
  function appendFolderRecovery(){
    const connect=node('button','secondary','Connect folder…');connect.type='button';
    connect.addEventListener('click',async()=>{
      if(working)return;working=true;
      try{
        const folder=await api('/api/choose-local-folder',{});
        if(folder.cancelled)return;
        await api('/api/fix-design/reconnect',{report_id:reportId,folder:folder.path});
      }catch(error){body.append(node('p','review-notice',error.message));}
      finally{working=false;}
      if(dialog.isConnected)start();
    });
    body.append(connect);
    // Typed route: the native dialog can fail to appear, but the backend
    // accepts a typed folder path too and verifies every file by hash.
    const typed=node('input','folder-path-input');typed.type='text';
    typed.placeholder='Or type the project folder path…';typed.maxLength=4096;
    const linkTyped=node('button','secondary','Link typed folder');linkTyped.type='button';
    linkTyped.addEventListener('click',async()=>{
      if(working)return;
      const folder=(typed.value || '').trim();
      if(!folder){body.append(node('p','review-notice','Type the folder you open in KiCad, then press Link typed folder.'));return;}
      working=true;
      try{
        await api('/api/fix-design/reconnect',{report_id:reportId,folder});
      }catch(error){body.append(node('p','review-notice',error.message));return;}
      finally{working=false;}
      if(dialog.isConnected)start();
    });
    body.append(typed,linkTyped,node('p','field-help','Your files stay unchanged. The folder is accepted only when every analyzed file matches.'));
  }
  async function start() {
    if(working || app.snapshot.report_id!==reportId)return;
    working=true;render();
    try{
      fix=await api('/api/fix-design/prepare',{report_id:reportId,steps:[{group_id:group.id}]});
      if(fix.status==='preparing'){polling=true;render();setTimeout(poll,3000);}
      else render();
    }catch(error){
      body.replaceChildren(node('p','review-notice',error.message));
      if(/folder/i.test(error.message || ''))appendFolderRecovery();
    }
    finally{working=false;}
  }
  if(existingId){working=true;api('/api/fix-design/'+existingId).then(current=>{fix=current;working=false;if(fix.status==='preparing'){polling=true;setTimeout(poll,3000);}render();}).catch(error=>{working=false;body.replaceChildren(node('p','review-notice',error.message));});}
  else start();
}
