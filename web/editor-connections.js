/* Read-only saved-file connections. Never trigger AI from a save notification. */
const editorConnectionsUI={busy:false,polling:false,catalog:{},connections:[],signature:''};

function editorConnectionLabel(record){
  if(record.status==='waiting')return 'Waiting for the editor';
  if(record.status==='stopped')return 'Add-in stopped';
  if(record.status==='saving')return 'Saving…';
  if(record.status==='unavailable')return 'File unavailable';
  if(record.review_outdated)return 'New save · review is out of date';
  if(record.editor==='fusion')return 'Fusion save received';
  return 'Watching saved files';
}

function renderEditorConnections(result){
  editorConnectionsUI.catalog=result.editors;editorConnectionsUI.connections=result.connections;
  const select=$('editor-kind');
  if(!select.options.length){
    for(const [key,editor] of Object.entries(result.editors)){
      const option=node('option','',editor.name);option.value=key;select.append(option);
    }
    select.value='kicad';
    editorFormatHelp();
  }
  const signature=JSON.stringify(result.connections);
  if(signature!==editorConnectionsUI.signature){
    editorConnectionsUI.signature=signature;
    const list=$('editor-connection-list');list.replaceChildren();
    if(!result.connections.length)list.append(node('p','field-help','No editors connected yet.'));
    for(const record of result.connections){
      const card=node('section','editor-connection');
      card.append(node('h3','',record.name),node('p','field-help',result.editors[record.editor].name),node('strong','',editorConnectionLabel(record)));
      const time=new Date(record.saved_at);
      if(record.saved_at)card.append(node('p','field-help',`Saved version ${record.revision} · ${Number.isNaN(time.valueOf())?'':time.toLocaleString()}`));
      if(record.status==='unavailable'||record.editor==='fusion')card.append(node('p','',record.message));
      if(record.addon_folder){
        const setup=node('details');setup.open=!record.revision;setup.append(node('summary','','Fusion setup'));
        setup.append(node('p','','Open your saved Electronics project in Fusion. In Scripts and Add-Ins, add the folder below and run PreflightSync. Save both the PCB and schematic to sync changes.'));
        setup.append(node('p','editor-file-path',record.addon_folder));
        setup.append(node('p','field-help','Uses Fusion’s preview Electronics API. Exports stay on this computer. The add-in never edits your Fusion design.'));
        card.append(setup);
      }
      if(!record.review_available)card.append(node('p','editor-coverage','File monitoring only. Analysis for this format is not available yet.'));
      const paths=node('details'),summary=node('summary','',`${record.files.length} connected ${record.files.length===1?'file':'files'}`);paths.append(summary);
      for(const path of record.files)paths.append(node('p','editor-file-path',path));
      card.append(paths);
      const actions=node('div','editor-actions');
      if(record.review_available){
        const use=node('button','secondary','Review saved files');use.type='button';use.disabled=record.status!=='watching'||editorConnectionsUI.busy;
        use.addEventListener('click',()=>useEditorConnection(record.id));actions.append(use);
      }
      const remove=node('button','text-button','Disconnect');remove.type='button';remove.disabled=editorConnectionsUI.busy;
      remove.addEventListener('click',()=>editorConnectionAction(()=>api('/api/editor-disconnect',{id:record.id})));
      actions.append(remove);card.append(actions);list.append(card);
    }
  }
  let banner=$('editor-save-notice');
  if(!banner){banner=node('div','editor-save-notice');banner.id='editor-save-notice';banner.setAttribute('role','status');$('workspace-view').prepend(banner);}
  const changed=result.connections.find(r=>r.current_report&&(r.review_outdated||r.status!=='watching'));
  const bannerKey=changed?`${changed.id}:${changed.revision}:${changed.status}`:'';
  if(banner.dataset.connectionState!==bannerKey){
    banner.dataset.connectionState=bannerKey;banner.replaceChildren();banner.hidden=!changed;
    if(changed){
      banner.append(node('strong','',changed.status==='watching'?'Your editor has newer saved files.':changed.status==='saving'?'Your editor is saving.':'A connected design file is unavailable.'),node('span','','This review describes the previous saved version.'));
      if(changed.status==='watching'){
        const update=node('button','secondary','Review saved files');update.type='button';update.addEventListener('click',()=>useEditorConnection(changed.id));banner.append(update);
      }
    }
  }
}

function editorFormatHelp(){
  const editor=editorConnectionsUI.catalog[$('editor-kind').value];if(!editor)return;
  $('editor-format-help').textContent=`${editor.help} Files: ${editor.extensions.join(', ')}.`;
  $('editor-coverage').textContent=editor.review_available?'Saved-file tracking + KiCad review':'Saved-file tracking only · native analysis is not available yet';
  $('pick-editor-files').textContent=editor.adapter==='fusion'?'Set up Fusion sync':'Select original files';
  $('editor-connect-form').querySelector('.editor-paths').hidden=editor.adapter==='fusion';
}

async function loadEditorConnections(){
  if(editorConnectionsUI.polling)return;
  editorConnectionsUI.polling=true;
  try{renderEditorConnections(await api('/api/editor-connections'));}
  finally{editorConnectionsUI.polling=false;}
}

async function editorConnectionAction(action){
  if(editorConnectionsUI.busy)return;
  editorConnectionsUI.busy=true;$('editor-connect-message').textContent='Connecting…';
  for(const button of $('editors-dialog').querySelectorAll('button:not(#close-editors)'))button.disabled=true;
  try{
    const result=await action();
    $('editor-connect-message').textContent=result?.cancelled?'Selection cancelled.':result?.disconnected?'Disconnected. Your files are unchanged.':result?.editor==='fusion'?'Add-in prepared. Follow Fusion setup above.':'Connected. Saves will be detected automatically.';
    if(!result?.cancelled)$('editor-file-paths').value='';
  }catch(error){$('editor-connect-message').textContent=error.message;}
  finally{
    editorConnectionsUI.busy=false;editorConnectionsUI.signature='';
    $('pick-editor-files').disabled=false;$('editor-connect-form').querySelector('[type="submit"]').disabled=false;
    try{await loadEditorConnections();}catch(error){$('editor-connect-message').textContent=error.message;}
  }
}

async function useEditorConnection(id){
  if(editorConnectionsUI.busy)return;
  if(app.busy){showError(new Error('Wait for the current check to finish before opening another saved version.'));return;}
  editorConnectionsUI.busy=true;
  try{
    const linked=await api('/api/editor-use',{id});app.config.linked_files=linked;connectOriginalFiles(linked);
    // Choose this project's own latest files, without reusing another board's baseline.
    const record=editorConnectionsUI.connections.find(r=>r.id===id);
    workflow.revisionBase=record?.current_report?app.snapshot.report_id:null;
    $('editors-dialog').close();navigate('workspace');
    $('connected-project-hint').textContent='Connected to the latest saved files. This review uses your current Vector settings.';
    $('run-form').requestSubmit();
  }catch(error){showError(error);}
  finally{editorConnectionsUI.busy=false;}
}

function initializeEditorConnections(){
  $('nav-editors').addEventListener('click',()=>{
    $('editors-dialog').showModal();loadEditorConnections().catch(error=>{$('editor-connect-message').textContent=error.message;});
  });
  $('close-editors').addEventListener('click',()=>$('editors-dialog').close());
  $('editor-kind').addEventListener('change',editorFormatHelp);
  $('pick-editor-files').addEventListener('click',()=>editorConnectionAction(()=>$('editor-kind').value==='fusion'?api('/api/editor-fusion-setup',{}):api('/api/choose-local-editor-files',{editor:$('editor-kind').value})));
  $('editor-connect-form').addEventListener('submit',event=>{
    event.preventDefault();editorConnectionAction(()=>api('/api/editor-connect',{editor:$('editor-kind').value,files:$('editor-file-paths').value.split('\n').map(p=>p.trim()).filter(Boolean)}));
  });
  async function pollConnections(){
    try{if(!document.hidden)await loadEditorConnections();}catch(error){if($('editors-dialog').open)$('editor-connect-message').textContent=error.message;}
    setTimeout(pollConnections,5000);
  }
  pollConnections();
}
