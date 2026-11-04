'use strict';
// Workspace features share the existing app's DOM helpers and API client.
const workflow = {offset:0,libraryRequest:0,baselineRequest:0,drafts:new Map(),comparison:null,pages:new Map(),inputReport:null,revisionBase:null};

// Display names for the bundled demos. IDs, folder names, KiCad files,
// source hashes and saved report references are never renamed — this mapping
// applies only to user-visible headings, so "falsch" still opens falsch.
const DEMO_DISPLAY_NAMES = {'ModularKeyboard wrong':'ModularKeyboard Wrong','ModularKeyboard correct':'ModularKeyboard Correct','falsch':'ModularKeyboard Wrong'};
function displayProjectName(name){return DEMO_DISPLAY_NAMES[name] || name;}

function clearSourceSelections() {
  for(const id of ['source-sch','source-pcb','source-bom','source-cpl'])$(id).value='';
}

function restoreInput() {
  const p=app.project, run=p?.workspace?.report_id;
  if(!run || workflow.inputReport===run)return;
  workflow.inputReport=run;
  workflow.revisionBase=null;
  $('revision-input-note').hidden=true;
  $('project-name').value=p.provenance?.project_name || p.archive.replace(/\.zip$/i,'');
  const source=p.workspace.input_source;
  if(source?.path){inputMode('path');$('project-path').value=source.path;}
  else if(source?.upload_name){inputMode('upload');if(app.upload?.name!==source.upload_name){app.upload=null;app.uploadFiles=null;if($('upload-label'))$('upload-label').textContent=`Upload ${source.upload_name} again to run another check`;}if(typeof renderUploader==='function')renderUploader();}
  else {inputMode('path');$('project-path').value='';$('project-path').placeholder='Select the source folder / ZIP again for this saved run';}
  for(const [id,kind] of [['source-sch','schematic'],['source-pcb','pcb'],['source-bom','bom'],['source-cpl','cpl']])$(id).value=p.selected_files?.[kind] || '';
  $('cpl-units').value=p.provenance?.options?.cpl_units || 'auto';
  $('use-native').checked=p.provenance?.options?.native!==false;
}

function paged(items,key,query,render) {
  const signature=`${app.snapshot.report_id || app.project?.archive}:${query}`;
  let page=workflow.pages.get(key);
  if(!page || page.signature!==signature){page={signature,limit:100};workflow.pages.set(key,page);}
  const more=items.length>page.limit?node('button','secondary',`Show more · ${page.limit} of ${items.length} displayed`):null;
  if(more)more.addEventListener('click',()=>{page.limit+=100;render();});
  return {items:items.slice(0,page.limit),more};
}

function decisionFor(group) {
  const decision=app.project?.workspace?.decisions?.[group.key];
  return decision?.signature===group.signature ? decision : {status:'open',note:'',revision:decision?.revision || 0};
}
function decisionEditor(group) {
  const form=node('form','decision-editor');
  if (!group.key || !app.project.workspace?.report_id) {form.append(node('p','field-help','Review decisions become available after this run is saved.'));return form;}
  const run=app.project.workspace.report_id, draftKey=run+':'+group.key;
  const saved=decisionFor(group), draft=workflow.drafts.get(draftKey) || saved;
  const label=node('label','','Your review decision'), select=node('select');select.id=`decision-${group.id}`;label.htmlFor=select.id;
  for (const [value,text] of [['open','Open — needs a decision'],['reviewed','Reviewed — note recorded'],['accepted_risk','Accepted risk — explain why']]) {const o=node('option','',text);o.value=value;select.append(o);}select.value=draft.status;
  const noteLabel=node('label','','Reason / review note'), input=node('textarea');input.rows=2;input.maxLength=4000;input.id=`note-${group.id}`;noteLabel.htmlFor=input.id;input.value=draft.note;input.placeholder='Record the evidence, intended exception, or next action.';
  const message=node('p','field-help'), save=node('button','secondary','Save decision'), discard=node('button','text-button','Discard draft');save.type='submit';save.dataset.decisionSave=group.key;save.disabled=app.busy;discard.type='button';
  const remember=()=>workflow.drafts.set(draftKey,{status:select.value,note:input.value,revision:draft.revision});select.addEventListener('change',remember);input.addEventListener('input',remember);
  discard.addEventListener('click',()=>{workflow.drafts.delete(draftKey);select.value=saved.status;input.value=saved.note;message.textContent='Draft discarded.';});
  const row=node('div','decision-actions');row.append(save,discard);
  form.append(node('h4','','Review journal'),label,select,noteLabel,input,row,message,node('p','field-help','Applies to this run only. The original finding stays in the report.'));
  if (saved.updated) form.append(node('p','basis',`Last saved ${new Date(saved.updated).toLocaleString()}`));
  const events=(app.project.workspace.events || []).filter(e=>e.finding_key===group.key);
  if (events.length) {const history=node('details','decision-history');history.append(node('summary','',`${events.length} recorded decisions`));list(history,events.map(e=>`${new Date(e.at).toLocaleString()} · ${e.status.replaceAll('_',' ')} · ${e.note}`));form.append(history);}
  form.addEventListener('submit',async e=>{
    e.preventDefault();save.disabled=true;message.textContent='Saving…';
    try {await api('/api/decision',{report_id:run,key:group.key,status:select.value,note:input.value,revision:draft.revision});workflow.drafts.delete(draftKey);await refresh();const card=$(group.id);if(card)card.open=true;}
    catch(error){message.textContent=error.message;message.classList.add('error');}
    finally{save.disabled=app.busy;}
  });
  return form;
}

function renderReadiness() {
  const data=app.project.workspace?.readiness, box=$('readiness');box.replaceChildren();if(!data)return;
  const head=node('div','readiness-heading'),text=node('div');text.append(node('div','eyebrow','BEFORE YOU ORDER'),node('h2','',data.headline));head.append(text,badge('Engineering review','review'));box.append(head);
  const grid=node('div','readiness-grid');
  for (const item of data.coverage) {const card=node('div','coverage-item');card.append(badge(item.status,item.status==='checked'?'completed':item.status==='missing'?'warning':''),node('strong','',item.label),node('p','',item.detail));grid.append(card);}
  const actions=node('details','next-actions');actions.append(node('summary','','Next steps & scope'));list(actions,data.next_actions);actions.append(node('p','field-help',data.note));box.append(grid,actions);
}

function renderAssembly() {
  const a=app.project.assembly || {}, box=$('assembly-content'), panel=node('div','panel assembly-overview');box.replaceChildren();
  const generated=app.project.assembly_export;
  if(generated?.counts){
    const details=node('details','assembly-assumptions');details.append(node('summary','','Export assumptions & checks'));
    list(details,generated.limits || []);
    details.append(node('p','field-help',`Previously supplied CSVs: ${a.bom_count || 0} BOM references and ${a.cpl_count || 0} placement references. Generated drafts do not count as an independent assembly-file comparison.`));
    box.append(details,node('h3','','Part assignments'),node('p','field-help','Select a component to inspect its datasheet or change its part.'));return;
  }
  panel.append(node('h3','',a.status==='compared'?'Assembly files compared with the PCB':'Assembly evidence is incomplete'),node('p','muted',`${a.bom_count || 0} BOM references · ${a.cpl_count || 0} placement references`));
  if (!a.bom) panel.append(node('p','','Add a BOM CSV to your project folder or ZIP. Include designators, values, footprints and any selected part numbers.'));
  if (!a.cpl) panel.append(node('p','','Add a placement CSV with reference, X/Y coordinates, rotation and board side.'));
  for (const kind of ['bom','cpl']) if(a[kind]) {panel.append(node('h4','',`${kind.toUpperCase()} columns recognized`));panel.append(table(['Field','CSV column'],Object.entries(a[kind].columns || {})));}
  list(panel,a.limits || ['Run the new checker to collect assembly and part-selection evidence.']);
  const action=node('button','secondary','Show assembly findings');action.addEventListener('click',()=>{selectTab('findings');$('finding-category').value='assembly';$('decision-filter').value='all';$('finding-search').value='';renderFindings();});panel.append(action);box.append(panel);
  box.append(node('h3','','Part selection worksheet'),node('p','field-help','A declared MPN or LCSC code is a starting point. Stock, package pinout, electrical ratings and substitutes are not verified. Export the review package to get this worksheet as CSV.'));
}

function renderParts() {
  const query=$('parts-search').value.toLowerCase().trim(), filter=$('parts-filter').value;
  const generated=app.project?.assembly_export?.parts;
  if(generated){
    const parts=generated.filter(p=>(['all','fitted'].includes(filter) || filter==='needs_part_number' && !p.code || filter==='conflicting_identity' && p.match==='unresolved') && JSON.stringify(p).toLowerCase().includes(query));
    const page=paged(parts,'parts',query+filter,renderParts);
    const rows=page.items.map(p=>[partSelectionLink(p.reference),p.value,p.code || 'Needed',p.mpn || '—',(p.match || 'unresolved').replaceAll('_',' '),p.placement]);
    $('parts-table').replaceChildren(rows.length?table(['Reference','Value','LCSC','Manufacturer part','Match basis','Placement'],rows):note('No parts match this view.'));
    if(page.more)$('parts-table').append(page.more);return;
  }
  const parts=(app.project?.assembly?.parts || []).filter(p=>(filter==='all' || filter==='fitted' && p.population==='fitted' || p.status===filter) && JSON.stringify(p).toLowerCase().includes(query));
  const page=paged(parts,'parts',query+filter,renderParts);
  const rows=page.items.map(p=>[evidenceLink(`C:${p.reference}`),p.value,p.footprint,p.mpn || '—',p.lcsc || '—',p.population,(p.status || 'unknown').replaceAll('_',' ')]);
  $('parts-table').replaceChildren(rows.length?table(['Reference','Value','Footprint','MPN','LCSC','Population','Identity'],rows):note('No parts match this view. Older reports need a new run to build the worksheet.'));
  if(page.more)$('parts-table').append(page.more);
}

async function loadLibrary() {
  const request=++workflow.libraryRequest;
  const params=new URLSearchParams({q:$('history-search').value,project:$('history-project').value,offset:workflow.offset,limit:30,latest:1});
  const data=await api('/api/library?'+params);
  if(request!==workflow.libraryRequest)return;
  const selected=$('history-project').value, options=[node('option','','All projects')];options[0].value='';
  for(const project of data.projects){const o=node('option','',`${project.name} (${project.runs})`);o.value=project.name;options.push(o);}
  $('history-project').replaceChildren(...options);$('history-project').value=selected;
  $('history-count').textContent=data.projects.length;
  $('history-page-label').textContent=data.total?`${data.grouped?data.total:data.projects.length} saved ${(data.grouped?data.total:data.projects.length)===1?'project':'projects'} · latest revisions`:data.projects.length?'No projects match your search.':'No projects yet';
  $('history-view').querySelector('.filterbar').hidden=!data.projects.length;
  $('history-view').querySelector('.pagination').hidden=data.total<=data.limit;
  $('history-prev').disabled=data.offset===0;$('history-next').disabled=data.offset+data.limit>=data.total;
  const seen=new Set();const runs=data.grouped?data.items:data.items.filter(run=>{const key=run.project || run.name;if(seen.has(key))return false;seen.add(key);return true;});
  // Bundled demo boards headline the page so a judge can open one with a
  // single click, as if it had just been uploaded. Saved reviews follow.
  let demos=[];
  try{demos=((await api('/api/demo-projects')).demos || []);}catch(error){demos=[];}
  const demoCards=demos.map(demo=>{
    const card=node('article','history-card'),content=node('div','history-card-content');
    const preview=node('div','project-preview');
    preview.append(node('span','preview-label','Bundled demo · Front'));
    // After a board check the saved preview replaces the placeholder, so the
    // library shows the actual PCB instead of claiming none is available.
    const previewRun=runs.find(run=>(run.project||run.name)===demo.name);
    if(previewRun && previewRun.has_preview!==false && previewRun.id){
      const image=node('img');
      image.alt=displayProjectName(demo.name)+' · saved board preview';image.loading='lazy';
      image.src='/reports/'+encodeURIComponent(previewRun.id)+'/board-front.svg';
      const missing=node('span','preview-unavailable','Preview available after a board check');
      missing.hidden=true;
      image.addEventListener('error',()=>{image.hidden=true;missing.hidden=false;},{once:true});
      preview.append(image,missing);
    }else preview.append(node('span','preview-unavailable','Preview available after a board check'));
    card.append(preview);
    content.append(node('h3','',displayProjectName(demo.name)),
      node('p','','Bundled demo board — no upload needed. Opens a disposable working copy for review; the originals stay pristine.'));
    const actions=node('div','history-card-actions');
    const open=node('button','primary','Open board →');
    open.addEventListener('click',async()=>{
      if(app.busy){showError(new Error('A check is already running. Wait for it to finish before opening another board.'));return;}
      open.disabled=true;
      try{
        const linked=await api('/api/demo-open',{name:demo.name});
        app.config.linked_files=linked;connectOriginalFiles(linked);
        app.project=null;app.fixDesigns=null;workflow.comparison=null;
        app.adding=false;
        app.revision=-1;navigate('workspace');await refresh();
      }catch(e){showError(e);}finally{open.disabled=false;}
    });
    const kicad=node('button','secondary','Open in KiCad');kicad.type='button';
    kicad.addEventListener('click',async()=>{
      if(app.busy){showError(new Error('A check is already running. Wait for it to finish before opening the board in KiCad.'));return;}
      kicad.disabled=true;
      try{await api('/api/demo-open-kicad',{name:demo.name});}
      catch(e){showError(e);}finally{kicad.disabled=false;}
    });
    const reset=node('button','secondary','Reset this project');reset.type='button';
    reset.addEventListener('click',async()=>{
      if(app.busy){showError(new Error('A check is already running. Wait for it to finish before resetting this project.'));return;}
      if(!confirm('Reset '+displayProjectName(demo.name)+' to its original state? This clears its saved reviews and answers and restores a fresh copy of the original board files.'))return;
      reset.disabled=true;
      try{const result=await api('/api/demo-reset',{name:demo.name});app.adding=false;app.revision=-1;if(result&&result.linked)navigate('workspace');await refresh();}
      catch(e){showError(e);}finally{reset.disabled=false;}
    });
    actions.append(open,kicad,reset);card.append(content,actions);return card;
  });
  const cards=runs.map(run=>{
    const card=node('article','history-card'),content=node('div','history-card-content'),meta=node('div','history-meta');
    const preview=node('div','project-preview');
    const image=node('img');image.alt=displayProjectName(run.project || run.name || 'Project')+' · saved PCB preview';image.loading='lazy';
    const missing=node('span','preview-unavailable','Preview available after a board check');
    if(run.has_preview===false){image.hidden=true;}else{image.src='/reports/'+encodeURIComponent(run.id)+'/board-front.svg';missing.hidden=true;}
    image.addEventListener('error',()=>{image.hidden=true;missing.hidden=false;},{once:true});
    preview.append(node('span','preview-label','Saved board · Front'),image,missing);card.append(preview);
    content.append(node('h3','',displayProjectName(run.project || run.name)),node('p','',run.headline));
    meta.append(node('span','',`Reviewed ${new Date(run.created).toLocaleString([], {dateStyle:'medium',timeStyle:'short'})}`),node('span','',`${run.components ?? '—'} components`),badge(run.ai_status==='completed'?'Vector response saved · open review for results':`Vector: ${(run.ai_status || 'not_run').replaceAll('_',' ')}`,run.ai_status));content.append(meta);
    const b=node('button','primary','Open review →');b.dataset.openReport=run.id;b.disabled=app.busy;
    b.addEventListener('click',async()=>{if(app.busy)return;app.pending=true;setBusy();clearError();try{app.adding=false;await api('/api/open-report',{id:run.id});workflow.comparison=null;navigate('workspace');await refresh();}catch(e){showError(e);}finally{app.pending=false;setBusy();}});
    const versions=node('details','project-versions');versions.append(node('summary','',`${run.version_count || data.projects.find(p=>p.name===run.project)?.runs || 1} saved versions`));
    versions.addEventListener('toggle',async()=>{
      if(!versions.open || versions.dataset.loaded)return;versions.dataset.loaded='loading';
      const body=node('div');versions.append(body);body.append(node('p','field-help','Loading saved versions…'));
      try{const history=await api('/api/library?'+new URLSearchParams({project:run.project || run.name,limit:100}));body.replaceChildren();
        for(const old of history.items){const row=node('button','text-button',`${new Date(old.created).toLocaleString()} · Automated review ${(old.ai_status || 'not_run').replaceAll('_',' ')}`);row.dataset.openReport=old.id;row.disabled=app.busy;
          row.addEventListener('click',async()=>{if(app.busy)return;app.pending=true;setBusy();try{app.adding=false;await api('/api/open-report',{id:old.id});workflow.comparison=null;navigate('workspace');await refresh();}catch(e){showError(e);}finally{app.pending=false;setBusy();}});body.append(row);}
        if(history.total>history.items.length)body.append(node('p','field-help',`Showing the latest ${history.items.length} versions.`));versions.dataset.loaded='ready';
      }catch(error){body.replaceChildren(node('p','field-help',error.message));delete versions.dataset.loaded;}
    });
    content.append(versions);const actions=node('div','history-card-actions');actions.append(b);card.append(content,actions);return card;
  });
  if(demoCards.length || cards.length)$('history-list').replaceChildren(...demoCards,...cards);
  else if(!data.projects.length){
    const empty=node('section','dashboard-empty');empty.append(node('h2','','Start with your KiCad project'),node('p','','Select the schematic, PCB and project files you already use in KiCad. We remember their paths so you can save changes and click Update.'));
    const add=node('button','primary','+ Add your project');add.type='button';add.addEventListener('click',addProject);empty.append(add);$('history-list').replaceChildren(empty);
  }else $('history-list').replaceChildren(note('No matching projects. Clear the search or choose another project.'));
}

async function loadBaselines() {
  const request=++workflow.baselineRequest, p=app.project;
  if(!p)return;
  const name=p.provenance?.project_name || p.archive.replace(/\.zip$/i,'');
  const runs=await api('/api/library?'+new URLSearchParams({project:name,limit:100}));
  if(request!==workflow.baselineRequest)return;
  const old=$('baseline-run').value, options=[];
  for(const run of runs.items.filter(r=>r.id!==app.snapshot.report_id)){const o=node('option','',`${new Date(run.created).toLocaleString()} · ${run.name}`);o.value=run.id;options.push(o);}
  if(!options.length){const o=node('option','','Run this project again to compare revisions');o.value='';options.push(o);}
  $('baseline-run').replaceChildren(...options);if(options.some(o=>o.value===old))$('baseline-run').value=old;
  if(workflow.comparison?.after!==app.snapshot.report_id){
    workflow.comparison=p.provenance?.revision_comparison ? {...p.provenance.revision_comparison,after:app.snapshot.report_id} : null;
    if(workflow.comparison){$('baseline-run').value=workflow.comparison.before;renderComparison(workflow.comparison);}
    else $('comparison-result').replaceChildren();
  }
}

function renderRevisionProgress() {
  const box=$('revision-progress');box.replaceChildren();
  const result=app.project?.provenance?.revision_comparison, p=result?.progress;
  const heading=node('div','revision-heading');
  const text=node('div');text.append(node('h2','',p?'Your changes, checked':'Made changes in KiCad?'),node('p','field-help',p?'Here’s what changed since the version you updated.':'Save your changes, then check the updated folder or upload a new ZIP. We’ll keep the old version and track your progress.'));
  const update=node('button','primary','Check my updated files');update.type='button';update.dataset.updateProject='';update.disabled=app.busy || !app.snapshot.report_id;
  update.addEventListener('click',updateSavedProject);
  heading.append(text,update);box.append(heading);
  if(!p)return;
  const counts=node('div','revision-counts');
  for(const [value,label] of [[p.cleared,'cleared'],[p.remaining,'still need work'],[p.new,'new'],[p.unverified,'couldn’t verify']])counts.append(node('span','',`${value} ${label}`));
  box.append(counts,node('p','field-help','A tick means the issue cleared in the checks that ran. It does not prove every instruction was followed or the hardware works.'));
  if(!p.files_changed)box.append(node('p','revision-notice','These look like the same files. Save your changes in KiCad before checking again.'));
  const details=node('details','revision-checklist');details.open=p.items.length<=6;details.append(node('summary','',`Show my progress · ${p.items.length} previous issues`));
  for(const item of p.items){
    const row=node('div',`revision-item ${item.status}`),label=node('label');
    const check=node('input');check.type='checkbox';check.disabled=true;check.checked=item.status==='cleared';check.setAttribute('aria-label',`${item.status==='cleared'?'Cleared':'Not cleared'}: ${item.title}`);
    label.append(check,node('strong','',item.title));row.append(label,node('p','field-help',item.reason));
    if(item.current_id){const link=node('button','text-link','See what’s left');link.type='button';link.addEventListener('click',()=>{selectTab('findings');$('finding-search').value=(app.project.review.groups.find(g=>g.id===item.current_id)?.title || item.title);$('finding-category').value='all';renderFindings();$('panel-findings').scrollIntoView({behavior:'smooth'});});row.append(link);}
    details.append(row);
  }
  if(!p.items.length)details.append(node('p','','There were no action items in the previous version.'));
  for(const item of result.groups.new.filter(g=>['fix','review'].includes(g.priority)&&g.category!=='coverage'))details.append(node('p','revision-notice',`New: ${item.title}`));
  box.append(details);
  const all=node('button','secondary','See all changes');all.type='button';all.addEventListener('click',()=>{selectTab('changes');$('panel-changes').scrollIntoView({behavior:'smooth'});});box.append(all);
}

function renderComparison(result) {
  const box=$('comparison-result');box.replaceChildren();const intro=node('div','panel comparison-summary');
  intro.append(node('h3','',`${result.groups.new.length} new · ${result.groups.changed.length} changed · ${result.groups.no_longer_reported.length} no longer reported`),node('p','',result.warning));
  if(result.lost_checks.length)list(intro,result.lost_checks.map(c=>'Unavailable now: '+c.replaceAll('_',' ')));box.append(intro);
  for(const [key,title] of [['new','New findings'],['changed','Changed findings'],['no_longer_reported','No longer reported']]){
    if(!result.groups[key].length)continue;const panel=node('div','panel detail-body');panel.append(node('h3','',title));
    for(const item of result.groups[key]){const g=item.after || item,row=node('p');row.append(node('span','',g.title+' '));if(key!=='no_longer_reported')row.append(evidenceLink(g.id));panel.append(row);}box.append(panel);
  }
  const parts=node('div','panel detail-body');parts.append(node('h3','','Component and placement changes'));
  list(parts,[`Added: ${result.components.added.join(', ') || 'none'}`,`Removed: ${result.components.removed.join(', ') || 'none'}`]);
  for(const c of result.components.changed){const detail=node('details','change-detail');detail.append(node('summary','',c.reference),table(['Revision','Records'],[['Before',JSON.stringify(c.before,null,2)],['After',JSON.stringify(c.after,null,2)]]));parts.append(detail);}
  if(!result.components.changed.length)parts.append(node('p','muted','No changed component fields or placement records.'));box.append(parts);
  const nets=node('div','panel detail-body');nets.append(node('h3','',`${result.nets.length} nets have different members`));
  if(result.nets.length)nets.append(table(['Net','Before','After'],result.nets.map(n=>[n.name,n.before.join(', '),n.after.join(', ')])));box.append(nets);
}

function initializeWorkflow() {
  $('cancel-revision').addEventListener('click',()=>{workflow.revisionBase=null;$('revision-input-note').hidden=true;setBusy();});
  $('project-name').value=app.config.default_path.split('/').filter(Boolean).pop()?.replace(/\.zip$/i,'') || 'My board';
  const select=node('select');select.id='decision-filter';select.setAttribute('aria-label','Filter review decisions');
  for(const [value,text] of [['all','All decisions'],['open','Open'],['reviewed','Reviewed'],['accepted_risk','Accepted risk']]){const o=node('option','',text);o.value=value;select.append(o);}
  $('finding-category').parentElement.append(select);select.addEventListener('change',renderFindings);
  for(const id of ['parts-search','parts-filter'])$(id).addEventListener('input',renderParts);
  let timer;
  $('history-search').addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>{workflow.offset=0;loadHistory().catch(showError);},220);});
  $('history-project').addEventListener('change',()=>{workflow.offset=0;loadHistory().catch(showError);});
  $('history-prev').addEventListener('click',()=>{workflow.offset=Math.max(0,workflow.offset-30);loadHistory().catch(showError);});
  $('history-next').addEventListener('click',()=>{workflow.offset+=30;loadHistory().catch(showError);});
  $('compare-button').addEventListener('click',async()=>{clearError();const baseline=$('baseline-run').value;if(!baseline){showError(new Error('Choose another run from this project first.'));return;}$('compare-button').disabled=true;try{workflow.comparison=await api('/api/compare',{before:baseline,after:app.snapshot.report_id});renderComparison(workflow.comparison);}catch(e){showError(e);}finally{$('compare-button').disabled=app.busy;}});
}
