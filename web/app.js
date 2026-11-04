'use strict';

const $ = id => document.getElementById(id);
// Temporary feature flag: the animated Command Center visualization is hidden
// while unreliable. Backend events, investigation data and review status stay
// intact; only the user-facing entry points are gated. (LiveScan milestone)
const SHOW_WORKFLOW_VIZ = false;
const categories = {electrical:'Circuit / connectivity', signal:'Signal paths', manufacturing:'Fabrication limits', assembly:'Assembly', documentation:'Schematic / libraries', cosmetic:'Cosmetic', expected:'Expected / excluded', coverage:'Checks unavailable'};
const app = {config:null, project:null, revision:-1, snapshot:{}, input:'path', upload:null, uploading:false, pending:false, busy:false, tab:'overview', completedJob:null, resumeAfterKey:null};
const natural = (a,b) => String(a).localeCompare(String(b), undefined, {numeric:true});
function node(tag, className='', text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}
function badge(text, kind='') { return node('span', `badge ${kind}`, text); }
function note(text) { return node('p', 'no-results', text); }
function showError(error) { app.connectionError=!!error.connectionError; $('error-text').textContent = String(error.message || error); $('error-banner').hidden = false; }
function configIsNewServer(fresh) { return !!(fresh && fresh.server_id && app.serverId && fresh.server_id !== app.serverId); }
function showServerUpdateBanner() {
  if (document.getElementById('server-update-banner')) return;
  const bar = node('div', 'server-update-banner'); bar.id = 'server-update-banner';
  bar.setAttribute('role', 'status');
  bar.append(node('span', '', 'PreFlight was updated or restarted. Refresh to load the latest interface. '));
  const go = node('button', 'primary', 'Refresh now'); go.type = 'button';
  go.addEventListener('click', () => location.reload());
  const later = node('button', 'secondary', 'Later'); later.type = 'button';
  later.addEventListener('click', () => bar.remove());
  bar.append(go, later);
  document.body.prepend(bar);
}
function clearError() { app.connectionError=false; $('error-banner').hidden = true; }
async function api(path, data, retried=false) {
  if(data===undefined)return sendApiRequest(path,data,retried);
  // Share a pending write between double clicks. Never repeat a write after a
  // timeout: the server may already have accepted it.
  app.inflightRequests ||= new Map();
  const key=JSON.stringify([path,data]);
  if(app.inflightRequests.has(key))return app.inflightRequests.get(key);
  const pending=sendApiRequest(path,data,retried);
  app.inflightRequests.set(key,pending);
  try{return await pending;}finally{app.inflightRequests.delete(key);}
}
async function sendApiRequest(path, data, retried=false) {
  const options = {cache:'no-store'};
  const controller=new AbortController();options.signal=controller.signal;
  const fixPaths=['/api/fix-design/approve','/api/fix-design/reject','/api/fix-design-export','/api/file-fix/apply','/api/file-fix/continue','/api/firmware/plan'];
  const timeout=setTimeout(()=>controller.abort(),['/api/chat','/api/step-help','/api/issue-proof','/api/schematic-preview'].includes(path)?180000:path.startsWith('/api/choose-local-')?125000:fixPaths.some(prefix=>path===prefix||path.startsWith(prefix+'/'))?180000:45000);
  if (data !== undefined) Object.assign(options, {method:'POST', headers:{'Content-Type':'application/json', 'X-PCB-Token':app.config.csrf_token}, body:JSON.stringify(data)});
  let response;
  let result;
  try { response = await fetch(path, options);result = await response.json(); }
  catch(error) {
    const write=data!==undefined;
    const failure=new Error(write?'The app did not confirm this action. It may still be running. Check its status before trying again.':error.name==='AbortError'?'The app took too long to respond. Reconnecting…':response?'The app returned an unreadable response. Reconnecting…':app.lastContact?'The local app stopped responding. This page will reconnect automatically.':'Cannot reach the local app. Open Start PCB Preflight.command to start it. This page will reconnect automatically.');
    failure.connectionError=!write;throw failure;
  }
  finally{clearTimeout(timeout);}
  app.lastContact=Date.now();
  if(response.status===403 && result?.error==='Refresh this page before trying again.' && !retried){
    const fresh=await api('/api/config');
    if(configIsNewServer(fresh))showServerUpdateBanner();
    else app.serverId=fresh.server_id;
    app.config=fresh;settingsStatus();return sendApiRequest(path,data,true);
  }
  if (!response.ok) {
    const failure=new Error(result?.error || `Request failed (${response.status}).`);
    failure.connectionError=data===undefined && result?.retryable===true;throw failure;
  }
  return result;
}
function settingsStatus(syncForm=false) {
  const hasKey = app.config.key_configured;
  const connectionTest=app.config.connection_test;
  const verified=connectionTest?.status==='completed' && connectionTest.model===app.config.model;
  $('ai-status').textContent = app.config.keychain_loading ? 'Checking saved provider key…' : 'Vector settings';
  // Configured credentials are not proof of a successful API request.
  $('ai-status').title = app.config.keychain_loading ? 'Checking macOS Keychain for a remembered provider key.' : verified ? 'Connection test passed for the selected model.' : hasKey ? 'API key saved. Use Save & test to verify it.' : 'Add an API key for optional Vector review';
  $('key-hint').textContent = app.config.keychain_loading ? 'Checking for Vector’s saved connection…' : verified ? 'Vector is connected. Ready to review.' : hasKey ? 'Key saved. Use Vector settings → Save & test to verify it.' : 'Connect Vector in Vector settings to add circuit review and chat.';
  if($('inline-key-entry'))$('inline-key-entry').hidden=hasKey;
  if($('inline-key-status'))$('inline-key-status').textContent=app.config.keychain_loading?'Checking macOS Keychain for your saved provider key…':hasKey?(app.config.key_saved?'Key remembered in macOS Keychain.':'API key configured for this session.'):'Paste your key to connect. Remember it on this Mac to reconnect after restarts.';
  $('settings-key-status').textContent = app.config.key_storage_error || (app.config.keychain_loading ? 'Checking macOS Keychain for your saved key…' : hasKey ? `${app.config.configured_keys || 1} key${(app.config.configured_keys || 1)===1?'':'s'} configured. Blank fields keep their current keys.` : 'Add your primary API key below.');
  if($('remember-key'))$('remember-key').disabled=app.config.keychain_available===false;
  if($('inline-remember-key'))$('inline-remember-key').disabled=app.config.keychain_available===false;
  if(syncForm || !$('settings-dialog').open){
    $('ai-model').value = 'meta';
    if($('meta-model'))$('meta-model').textContent='muse-spark-1.3-contributor';
    if($('ai-usage-mode'))$('ai-usage-mode').value='complete';
  }
  if($('meta-key-status'))$('meta-key-status').textContent=verified && app.config.provider==='Meta'?'Meta connection test passed. Vector is ready.':app.config.meta_key_configured?'Meta key saved. Use Save & test to verify it.':'Add a key from Meta’s own API account. Model availability depends on your account.';
  providerSettings();
  usageSettings();
  if(app.config.keychain_loading && !app.keychainStatusTimer && (app.keychainStatusAttempts || 0) < 20) {
    app.keychainStatusAttempts=(app.keychainStatusAttempts || 0)+1;
    app.keychainStatusTimer=setTimeout(async()=>{
      app.keychainStatusTimer=null;
      try { app.config=await api('/api/config'); settingsStatus(); } catch {}
    },1500);
  }
}
function providerSettings(){
  if($('meta-key-fields'))$('meta-key-fields').hidden=false;
  if($('settings-key-status'))$('settings-key-status').textContent=app.config.key_storage_error || (app.config.keychain_loading?'Checking saved keys…':app.config.meta_key_configured?'Meta key is configured. A blank field keeps it.':'Add your Meta key above.');
}
function usageSettings(){
  if(!$('ai-usage-help'))return;
  const limits=app.config.ai_request_limits || {review:12,repair:20,chat:4,firmware:8,connection:2};
  $('ai-usage-help').textContent='Complete investigation: Vector plans an investigation, gathers targeted evidence, then reviews and challenges its conclusions. '
    + `Up to ${limits.review} requests per review, ${limits.repair} per repair action, ${limits.chat} per chat answer and ${limits.firmware} per firmware plan, including retries. `
    + 'Limits reset for each action; the euro spending cap in usage tracking still applies.';
}
function setBusy() {
  app.busy = app.pending || app.uploading || app.snapshot.job?.status === 'running';
  if($('ai-usage-mode'))$('ai-usage-mode').disabled=app.busy;
  for (const id of ['run-button','ask-button','save-settings','test-ai','forget-key','meta-api-key','ai-model','zip-file','project-path','project-name','source-sch','source-pcb','source-bom','source-cpl','cpl-units','use-ai','use-native','initial-question','compare-button','cancel-revision','inline-connect','inline-api-key','select-original-files','choose-local-folder','use-connected-project']) if($(id))$(id).disabled = app.busy;
  if($('connect-project-files'))$('connect-project-files').disabled=app.busy;
  for (const b of document.querySelectorAll('[data-decision-save]')) b.disabled=app.busy || !app.snapshot.report_id;
  $('run-button').textContent = app.busy ? (app.uploading ? 'Uploading ZIP…' : 'Checking your board…') : (workflow.revisionBase ? 'Check saved changes' : 'Check my board');
  for (const b of document.querySelectorAll('[data-update-project]')) b.disabled=app.busy || !app.snapshot.report_id;
  for (const b of document.querySelectorAll('[data-open-report]')) b.disabled = app.busy;
  for (const b of document.querySelectorAll('[data-design-action]')) b.disabled = !app.snapshot.report_id || (b.dataset.designAction==='chat' ? (typeof designUI!=='undefined' && designUI.chatBusy) : app.busy);
  if($('quick-update'))$('quick-update').disabled=app.busy || (!app.snapshot.report_id && !app.upload);
  if($('product-details'))$('product-details').disabled=app.busy;
  $('downloads').hidden = !app.snapshot.report_id || app.busy;
  for(const link of document.querySelectorAll('[data-assembly-download]'))link.hidden=app.busy || !app.snapshot.report_id;
}
function navigate(view) {
  const viewChanged=app.view!==view;
  app.view=view;
  // Opening a link within the current view should not throw away the reader's
  // position. A fresh page selection still starts at its heading.
  if(viewChanged && typeof window!=='undefined')window.scrollTo?.({top:0,behavior:'instant'});
  $('workspace-view').hidden = view !== 'workspace';
  $('history-view').hidden = view !== 'history';
  if($('chat-view'))$('chat-view').hidden=view!=='chat';
  if($('checks-view'))$('checks-view').hidden=view!=='checks';
  if($('start-view'))$('start-view').hidden=view!=='start';
  if($('livescan-view'))$('livescan-view').hidden=view!=='livescan';
  for (const name of ['start','workspace','history','chat','checks','livescan']) {
    if(!$(`nav-${name}`))continue;
    $(`nav-${name}`).classList.toggle('active', name === view);
    if (name === view) $(`nav-${name}`).setAttribute('aria-current','page');
    else $(`nav-${name}`).removeAttribute('aria-current');
  }
  $('breadcrumb').textContent = view === 'checks' ? 'What we check' : view === 'chat' ? 'Chat' : view === 'workspace' ? 'Board review' : view === 'livescan' ? 'Breadboard to PCB' : view === 'start' ? 'Start' : 'Projects';
  if (view === 'history') loadHistory().catch(showError);
  if (view === 'livescan' && typeof window.initializeLiveScan === 'function') window.initializeLiveScan();
}
function addProject(){
  if(app.busy){showError(new Error('Wait for the current check to finish before adding a project.'));return;}
  clearError();workflow.revisionBase=null;app.config.linked_files=null;app.upload=null;
  $('job-panel').before($('setup-disclosure'),$('source-panel'));
  app.adding=true;$('setup-disclosure').open=true;
  if($('product-details'))$('product-details').hidden=true;
  $('setup-summary').textContent='Project files & check settings';
  inputMode('path');clearSourceSelections();clearUploader();$('project-path').value='';$('project-name').value='My board';
  $('results').hidden=true;$('job-panel').hidden=true;$('empty-state').hidden=true;
  $('project-title').textContent='Add project';$('project-subtitle').textContent='Connect the files you edit in KiCad.';
  $('connected-file-list').replaceChildren();$('connected-project-hint').textContent='Select your files once. We remember their paths. Save in KiCad, then click Update to read your saved changes.';
  $('source-panel').open=false;$('quick-update').hidden=true;
  navigate('workspace');
}
function jobElapsedText(job){
  const seconds=Math.max(0,Math.round(((job.ended?new Date(job.ended):Date.now())-new Date(job.started))/1000));
  return !Number.isFinite(seconds)?'—':seconds<60?`${seconds}s`:`${Math.floor(seconds/60)}m ${seconds%60}s`;
}
function renderWorkflowStrip(){
  const strip=$('workflow-strip');if(!strip)return;
  const job=app.snapshot?.job,pipe=app.project?.ai_review?.pipeline,pipeline=typeof pipe==='string'?null:pipe;
  let status='',elapsed='—',dot='idle';
  if(job&&job.status==='running'){status=`Vector investigation · ${job.message||'working'}`;elapsed=jobElapsedText(job);dot='running';}
  else if(pipeline&&typeof pipeline==='object'){status=`Vector investigation · ${pipeline.status||'recorded'}`;elapsed=typeof pipeline.elapsed_seconds==='number'?`${Math.round(pipeline.elapsed_seconds)}s`:'—';dot=pipeline.status==='failed'?'failed':'done';}
  else if(app.project?.ai_review){status=`Vector review ${app.project.ai_review.status||'done'} · workflow data unavailable`;dot=app.project.ai_review.status==='failed'?'failed':'done';}
  else if(app.project){status='No investigation recorded yet';dot='idle';}
  else{strip.hidden=true;return;}
  strip.hidden=false;
  $('workflow-status-text').textContent=status;
  $('workflow-elapsed').textContent=elapsed;
  $('workflow-dot').className=`workflow-dot ${dot}`;
}
function initializeReviewTabs(){
  // Delegated on the tablist: tabs added later (overview, manufacturing,
  // release, project) work without rebinding, and keyboard order always
  // reflects the live row. Binding twice is harmless.
  const list=document.querySelector('[role=tablist]');
  if(!list||list.dataset.tabsBound)return;list.dataset.tabsBound='1';
  list.addEventListener('click',event=>{
    const button=event.target&&event.target.closest?event.target.closest('[data-tab]'):null;
    if(button&&list.contains(button))selectTab(button.dataset.tab);
  });
  list.addEventListener('keydown',event=>{
    if(!['ArrowRight','ArrowLeft','Home','End'].includes(event.key))return;
    const visible=[...list.querySelectorAll('[data-tab]')].filter(tab=>!tab.classList.contains('advanced-tab'));
    const current=visible.find(tab=>tab===event.target||tab.contains(event.target));
    if(!current)return;
    const i=visible.indexOf(current);let next;
    if(event.key==='ArrowRight')next=(i+1)%visible.length;
    else if(event.key==='ArrowLeft')next=(i+visible.length-1)%visible.length;
    else if(event.key==='Home')next=0;else next=visible.length-1;
    event.preventDefault();selectTab(visible[next].dataset.tab);visible[next].focus();
  });
}

function selectTab(name) {
  name=reviewSection(name);
  const section=['connections','board','notes'].includes(name);
  app.tab = section ? 'overview' : name;
  if(section && overviewUI.section!==name)chooseReviewSection(name);
  for(const panel of document.querySelectorAll('#results [role=tabpanel]'))panel.hidden=panel.id!==`panel-${app.tab}`;
  syncReviewNavigation();
  renderSelectedDetails();
}

function syncReviewNavigation(){
  const selected=app.tab==='overview'?overviewUI.section:app.tab;
  for(const button of document.querySelectorAll('[data-tab]')){
    const active=button.dataset.tab===selected;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
  }
  if($('more-view'))$('more-view').value=primaryViews.includes(selected) || selected==='chat'?'':selected;
  $('panel-overview')?.setAttribute('aria-labelledby',selected==='notes'?'more-view':`tab-${overviewUI.section}`);
}
function renderSelectedDetails(){
  if(!app.project)return;
  const renders={findings:renderFindings,ai:renderAI,components:renderComponents,nets:renderNets,coverage:renderCoverage,raw:renderRaw,datasheets:renderDatasheets,actions:renderChangePlan,manufacturing:renderManufacturing,release:renderRelease};
  if(app.tab==='chat')renderChatHome();
  renders[app.tab]?.();
  if(app.tab==='coverage')renderRaw();
}
function evidenceLink(id) {
  const button = node('button','chip',id.startsWith('C:') || id.startsWith('N:') ? id.slice(2) : id);
  button.type = 'button';
  button.addEventListener('click', () => focusEvidence(id));
  return button;
}
function focusEvidence(id) {
  navigate('workspace');
  let target;
  if (/^R\d+$/.test(id)) {
    selectTab('findings'); $('finding-category').value = 'all'; $('finding-search').value = ''; renderFindings(); target = $(id);
  } else if (id.startsWith('C:')) {
    selectTab('components'); $('component-search').value = id.slice(2); renderComponents(); target = $(`component-${id.slice(2)}`);
  } else if (id.startsWith('N:')) {
    selectTab('nets'); $('net-search').value = id.slice(2); renderNets(); target = $(`net-${id.slice(2)}`);
  } else if (/^F\d+$/.test(id)) {
    selectTab('coverage'); $('raw-severity').value = 'all'; $('raw-search').value = id; renderRaw(); target = $(id);
  }
  if (target) { target.open = true; target.scrollIntoView({behavior:'smooth', block:'center'}); }
}
function sectionText(parent, label, value, className='') {
  if (!value) return;
  parent.append(node('h4','',label), node('p',className,value));
}
function list(parent, values) {
  const ul = node('ul','evidence-list');
  for (const value of values || []) ul.append(node('li','',typeof value === 'string' ? value : JSON.stringify(value)));
  parent.append(ul);
}
function sourceLinks(parent, sources) {
  // A retrieved datasheet does not always carry a product URL; the citation
  // title (manufacturer, page) must still render instead of being dropped.
  for (const source of sources || []) {
    const p = node('p');
    let linked = false;
    if (source.url) {
      try {
        const url = new URL(source.url);
        if (['https:','http:'].includes(url.protocol)) {
          const a = node('a','text-link', source.title || source.label || source.url);
          a.href = url.href; a.target = '_blank'; a.rel = 'noreferrer';
          p.append(a); linked = true;
        }
      } catch { /* fall through to plain-text rendering */ }
    }
    if (!linked) {
      const title = source.title || source.label;
      if (!title) continue;
      p.append(node('span','', title));
    }
    if (source.section || source.pages) p.append(node('span','muted',` · ${source.section || source.pages}`));
    parent.append(p);
  }
}
function renderFindings() {
  const query = $('finding-search').value.toLowerCase().trim(), filter = $('finding-category').value;
  const all = app.project?.review?.groups || [];
  const decisionFilter=$('decision-filter')?.value || 'all';
  const groups = all.filter(g => (filter === 'all' || (filter === 'attention' ? ['fix','review'].includes(g.priority) : g.category === filter)) && (decisionFilter==='all' || decisionFor(g).status===decisionFilter) && JSON.stringify(g).toLowerCase().includes(query));
  $('finding-count').textContent = `${groups.length} of ${all.length} review groups · Expand a group for evidence and the next step.`;
  const elements = groups.map(g => {
    const card = node('details','finding'); card.id = g.id;
    const summary = node('summary'); summary.append(node('span','card-id',g.id));
    const main = node('div','finding-main'), badges = node('div','badges');
    badges.append(badge(g.priority === 'fix' ? 'Fix' : g.priority === 'review' ? 'Review' : 'Note',g.priority), badge(categories[g.category] || g.category));
    if (decisionFor(g).status!=='open') badges.append(badge(decisionFor(g).status.replaceAll('_',' '),'completed'));
    main.append(badges, node('h3','',g.title), node('p','',g.summary)); summary.append(main); card.append(summary);
    const body = node('div','finding-body');
    sectionText(body,'Why it matters',g.why);
    sectionText(body,'Next step',g.next_step,'next-step');
    if (g.components?.length) { body.append(node('h4','','Components')); const refs = node('div','chips'); for (const ref of g.components) refs.append(evidenceLink(`C:${ref}`)); body.append(refs); }
    if (g.evidence?.length) { body.append(node('h4','','Evidence')); list(body,g.evidence); }
    sourceLinks(body,g.sources);
    if (g.issue_ids?.length) { body.append(node('h4','',`${g.raw_count} underlying rule findings`)); const ids = node('div','chips'); for (const id of g.issue_ids) ids.append(evidenceLink(id)); body.append(ids); }
    body.append(node('p','basis',`${g.basis || 'Local interpretation'} · ${g.confidence || 'Review required'}`));
    body.append(decisionEditor(g));card.append(body); return card;
  });
  $('finding-list').replaceChildren(...(elements.length ? elements : [note('No groups match this view. Try “All findings” or another search.')]));
}
const replayActors={vector:'Vector',scope:'Scope · coverage',circuit:'Circuit · systems',scenario:'Scenario review',verdict:'Verdict · evidence judgment',origin:'Origin · product model',doubt:'Doubt · blind review',fault:'Fault · skeptical review',gauge:'Gauge · confidence audit'};
function cappedChips(parent, ids, overflow, prefix='') {
  if(!ids.length && !overflow)return;
  const chips=node('div','chips');
  for(const id of ids)chips.append(prefix ? evidenceLink(prefix+id) : node('span','chip',id));
  parent.append(chips);
  if(overflow)parent.append(node('p','field-help',`+${overflow} more`));
}
function renderCoverageProof(panel, proof) {
  if(!proof)return;
  const section=node('details');
  section.append(node('summary','','Coverage proof'));
  section.append(node('p','',`${proof.claims_total} hypotheses tracked. Counts come from recorded entries only.`));
  const order=['supported','contradicted','unresolved','not_applicable','hypothesized','under_investigation'];
  const chips=node('div','chips');
  for(const status of order)chips.append(node('span','chip',`${status}: ${(proof.by_status||{})[status]||0}`));
  section.append(chips);
  const origins=Object.keys(proof.by_origin||{}).sort();
  if(origins.length){section.append(node('h4','','Origins'));list(section,origins.map(o=>`${o}: ${proof.by_origin[o]}`));}
  if(proof.examined_components?.items?.length){section.append(node('h4','','Examined components'));cappedChips(section,proof.examined_components.items,proof.examined_components.overflow,'C:');}
  if(proof.examined_nets?.items?.length){section.append(node('h4','','Examined nets'));cappedChips(section,proof.examined_nets.items,proof.examined_nets.overflow,'N:');}
  if((proof.verification||[]).length){
    section.append(node('h4','','Per-finding verification'));
    for(const item of proof.verification){
      const entry=node('details');
      entry.append(node('summary','',`${item.id}: ${item.final||'unrecorded'} · evidence ${item.gate1||'?'} · judge ${item.judge||'?'}`));
      entry.append(node('p','',item.statement||''));
      if(item.final_reason)entry.append(node('p','field-help',item.final_reason));
      if((item.gate1_checks||[]).length){entry.append(node('h4','','Deterministic checks'));list(entry,item.gate1_checks.map(c=>`${c.name}: ${c.result} — ${c.reason}`));}
      if(item.judge_reason)entry.append(node('p','',`Independent judge (${item.judge}): ${item.judge_reason}`));
      if((item.reinvestigations||[]).length){entry.append(node('h4','','Reinvestigation'));list(entry,item.reinvestigations.map(a=>`${a.action}: ${a.detail}`));}
      if(typeof item.gauge_sureness==='number')entry.append(node('p','field-help',`Gauge sureness: ${item.gauge_sureness}/100 (AI estimate, not proof)`));
      if((item.open_questions||[]).length){entry.append(node('h4','','Open questions'));list(entry,item.open_questions);}
      section.append(entry);
    }
    if(proof.verification_overflow)section.append(node('p','field-help',`+${proof.verification_overflow} more verified findings`));
  }
  for(const item of proof.unresolved || []){
    const entry=node('details');
    entry.append(node('summary','',`${item.id}: unresolved`));
    entry.append(node('p','',item.statement));
    if(item.open_questions?.length){entry.append(node('h4','','Open questions'));list(entry,item.open_questions);}
    section.append(entry);
  }
  if(proof.unresolved_overflow)section.append(node('p','field-help',`+${proof.unresolved_overflow} more unresolved`));
  const gaps=proof.gaps||{};
  if((gaps.unexamined_claimable||[]).length){section.append(node('h4','','Never examined'));list(section,gaps.unexamined_claimable);}
  if((proof.intent_questions||[]).length){section.append(node('h4','','Intent questions'));list(section,proof.intent_questions.map(q=>q.question));}
  for(const limitation of proof.limitations || [])section.append(node('p','field-help',limitation));
  panel.append(section);
}
function renderReplay(panel, replay) {
  if(!replay || !replay.length)return;
  const section=node('details');
  section.append(node('summary','','Investigation replay'));
  for(const entry of replay){
    const item=node('details');
    item.append(node('summary','',`${entry.seq}. ${entry.title}`));
    if(entry.claim_id)item.append(node('p','',`${entry.claim_id}${entry.statement?': '+entry.statement:''}`));
    if(entry.detail)item.append(node('p','',entry.detail));
    item.append(node('p','field-help',replayActors[entry.actor] || entry.actor || 'recorded'));
    section.append(item);
  }
  panel.append(section);
}
function renderInvestigation(box, pipeline) {
  if(!pipeline)return;
  const panel=node('details','panel'), tasks=pipeline.tasks || [];
  panel.append(node('summary','','How Vector investigated this design'));
  if(SHOW_WORKFLOW_VIZ){
    const deck=node('a','',('Open the Investigation Command Center'));
    deck.href='/command'; deck.target='_blank'; deck.rel='noopener';
    const deckline=node('p','field-help','');
    deckline.append(deck); panel.append(deckline);
  }
  if(pipeline.status)panel.append(node('p','field-help',`Investigation ${pipeline.status}`));
  if(pipeline.product_hypothesis)panel.append(node('p','',`Working understanding: ${pipeline.product_hypothesis}`));
  panel.append(node('p','field-help','Tools gather evidence. Accepted explanations and completed tool calls do not certify the board.'));
  for(const step of pipeline.steps || []){
    panel.append(node('h3','',step.title),node('p','',`${step.status==='completed'?'Completed':'Limited'} · ${step.detail}`));
  }
  for(const task of tasks){
    const item=node('details');
    const status={evidence_gathered:'Evidence gathered',limited:'Evidence has limits',unavailable:'Evidence unavailable'}[task.status] || 'Pending';
    const enrich=(text,form='full')=>typeof withComponentContext==='function'?withComponentContext(text,form):text;
    item.append(node('summary','',enrich(`${(task.references || []).join(', ')} · ${task.question}`,'short')),
      node('p','',`${status} · ${String(task.tool || '').replaceAll('_',' ')}`),node('p','',enrich(task.reason)));
    for(const limitation of task.limitations || [])item.append(node('p','field-help',limitation));
    panel.append(item);
  }
  for(const limitation of pipeline.limitations || [])panel.append(node('p','field-help',limitation));
  renderCoverageProof(panel, pipeline.coverage_proof);
  renderReplay(panel, pipeline.replay);
  box.append(panel);
}

function renderAI() {
  const ai = app.project.ai_review || {}, box = $('ai-content'); box.replaceChildren();
  if(app.snapshot.job?.kind==='review' && app.snapshot.job.status==='running'){
    box.append(node('h2','','Vector is reviewing this saved board…'),node('p','','The activity log above shows progress. Your previous checks remain available; the old Vector result will be replaced when this review is saved.'));
    return;
  }
  if(ai.status==='needs_context'){
    box.append(node('h2','','Files read · a few details are missing'),node('p','',ai.message));
    const answer=node('button','primary','Answer questions from the files');answer.type='button';answer.disabled=app.busy;
    answer.addEventListener('click',()=>editProductContext(false).catch(showError));box.append(answer);return;
  }
  if(ai.request_usage){
    const u=ai.request_usage;
    box.append(node('p','field-help',`${u.requests} request${u.requests===1?'':'s'} used · ${u.request_limit-u.requests} remaining — limit ${u.request_limit} per action, resets each action (usage, not failures)${ai.reused?' · unchanged review reused':''}`));
  }
  if(ai.review_scope)box.append(node('p','field-help',ai.review_scope));
  renderInvestigation(box, ai.pipeline);
  if (ai.status !== 'completed' || !ai.result) {
    const panel = node('div','panel ai-unavailable');
    const titles = {running:'Vector is reviewing the evidence…', disabled:'Vector review was off for this run.', failed:'Vector could not complete this review.', not_configured:'Connect Vector to add an Vector review.'};
    panel.append(node('h2','',titles[ai.status] || 'Add an Vector interpretation.'),node('p','',ai.message || 'Open Chat to ask about the saved review, or connect Vector in settings.'));
    const hint = aiFailureHint(ai);
    if (hint) panel.append(node('p','ai-diagnosis',hint));
    if(ai.status!=='running')panel.append(aiRetryButton(),node('p','field-help','Runs Vector on this saved revision with your current product context. Existing local checks and datasheets are reused.'));
    if(!String(ai.message || '').includes('AI evidence limit')){
      const b = node('button','secondary',app.config.key_configured ? 'Review settings & connection test' : 'Set up Vector'); b.addEventListener('click',openSettings); panel.append(b);
    }
    box.append(panel);
    if(typeof renderQuestionAnswers==='function')renderQuestionAnswers(box);
    return;
  }
  const result = ai.result, intro = node('div','panel ai-summary'), badges = node('div','badges');
  badges.append(badge('Vector interpretation','completed'),badge(ai.model || 'Vector'));
  if(ai.providers_used?.some(p=>p.fallback))badges.append(badge('Gemini backup used','warning'));
  intro.append(badges,node('p','',result.summary));
  if (ai.question) intro.append(node('p','muted',`Your question: ${ai.question}`));
  if (result.answer) intro.append(node('p','answer',result.answer));
  box.append(intro);
  if(ai.rejected_items?.length){
    const partial=node('div','panel'),coverage=ai.coverage || {};
    partial.append(node('h3','','Review complete · some conclusions remain unresolved'),node('p','',`${coverage.validated_actions || 0} of ${coverage.requested_actions || 0} issue explanations have supporting evidence. ${ai.rejected_items.length} conclusion${ai.rejected_items.length===1?' remains':'s remain'} unresolved after the automatic correction. Supported advice is available; unresolved suggestions have not been added as confirmed faults.`));
    const details=node('details');details.append(node('summary','','Review diagnostics'));
    for(const item of ai.rejected_items){
      const label=item.group_id || item.reference || 'Review';
      const message=String(item.message || 'The suggestion did not pass the evidence checks.');
      details.append(node('p','',message.startsWith(`${label}:`) ? message : `${label}: ${message}`));
    }
    partial.append(details);box.append(partial);
  }
  renderCircuitAudit(box);
  for (const f of result.findings || []) {
    const card = node('article','panel finding-body'), badges = node('div','badges');
    badges.append(badge(categories[f.impact] || f.impact),badge(`${f.confidence} confidence`));
    card.append(badges,node('h3','',f.title),node('p','',f.interpretation));
    sectionText(card,'Next step',f.next_step,'next-step');
    const refs = node('div','chips'); for (const id of f.evidence_ids || []) refs.append(evidenceLink(id)); card.append(refs); box.append(card);
  }
  const limits = node('div','panel detail-body');
  if(typeof renderQuestionAnswers==='function')renderQuestionAnswers(limits);
  limits.append(node('h4','','Limits of this review')); list(limits,result.limitations || []);
  limits.append(node('p','basis','Vector uses the structured evidence from this run. It does not retrieve every datasheet or certify your board.')); box.append(limits);
}
function aiFailureHint(ai) {
  const message=String(ai.message || '').toLowerCase(), code=ai.diagnostics?.http_status;
  if(message.includes('ai evidence limit'))
    return 'This saved run stopped before any Vector request was sent. The former evidence-size limit has been removed. Select Retry Vector review to review this saved revision; this old error does not mean your key or credits failed.';
  if ((code===429 || message.includes('quota or rate limit')) && (ai.provider==='OpenAI' || message.includes('openai')))
    return 'OpenAI reached a rate or billing limit. Check API usage and billing, then retry. The job status shows whether your Gemini backup was used.';
  if ((code===429 || message.includes('quota or rate limit') || message.includes('too_many_requests')) && ai.provider!=='Meta')
    return 'This is a quota or rate-limit error. A replacement key only helps if it belongs to a Google project with available quota; a new key from the same project shares that project’s limits.';
  if (code===429 || message.includes('quota or rate limit') || message.includes('too_many_requests'))
    return 'Meta’s API reported a rate or billing limit. Check your Meta usage and billing, then retry. Replacing the key only helps if the new key has available quota.';
  if (code===503 || message.includes('http 503') || message.includes('service_unavailable'))
    return 'Vector’s provider reported that the model is temporarily busy. A paid key may not fix this; try again later or choose another model in Vector settings.';
  if (code===400 || message.includes('invalid_argument') || message.includes('structured outputs'))
    return 'Vector’s provider rejected the request format. This is a request or model compatibility problem, not an exhausted-credit message.';
  if (code===401 || message.includes('api key was rejected'))
    return 'The provider rejected this API key. Replace it in Vector settings, then use Save & test.';
  if (message.includes('evidence checks') || message.includes('invented') || message.includes('unverified quotation'))
    return 'The API returned an answer, but the app rejected claims it could not match to the saved schematic, board or supplied datasheet evidence. Replacing the key usually will not fix this.';
  return '';
}
function table(headers, rows) {
  const wrap = node('div','table-wrap'), t = node('table','detail-table'), head = node('thead'), tr = node('tr');
  for (const header of headers) tr.append(node('th','',header)); head.append(tr); t.append(head);
  const body = node('tbody');
  for (const row of rows) { const r = node('tr'); for (const value of row) { const cell = node('td'); cell.append(value instanceof Node ? value : document.createTextNode(String(value ?? '—'))); r.append(cell); } body.append(r); }
  t.append(body); wrap.append(t); return wrap;
}
function renderComponents() {
  const query = $('component-search').value.toLowerCase().trim();
  const components = Object.values(app.project?.components || {}).sort((a,b)=>natural(a.reference,b.reference)).filter(c => JSON.stringify(c).toLowerCase().includes(query));
  const page=paged(components,'components',query,renderComponents);
  const cards = page.items.map(c => {
    const card = node('details','component'); card.id = `component-${c.reference}`;
    const summary = node('summary'), description = node('div','component-description'), record = c.schematic[0] || c.pcb[0] || c.bom?.[0] || {};
    summary.append(node('span','component-ref',c.reference)); description.append(node('strong','',record.value || '(no value)'),node('small','',record.footprint || 'No footprint assigned')); summary.append(description);
    if (record.dnp) summary.append(badge('DNP'));
    if (!c.schematic.length || !c.pcb.length) summary.append(badge(!c.schematic.length ? 'PCB only' : 'Schematic only','review'));
    card.append(summary);
    card.addEventListener('toggle', () => {
      if (!card.open || card.dataset.loaded) return;
      card.dataset.loaded = 'true'; const body = node('div','detail-body');
      for (const [kind, records] of [['Schematic',c.schematic],['PCB',c.pcb]]) {
        for (const r of records) {
          const block = node('div','source-record'); block.append(node('strong','',kind),node('p','',`${r.value || 'No value'} · ${r.footprint || 'No footprint'}`),node('p','mono',`${r.source_file}:${r.line}`));
          const properties = Object.entries(r.properties || {}).filter(([k,v])=>v && !['Reference','Value','Footprint'].includes(k));
          if (properties.length) block.append(table(['Property','Value'],properties));
          body.append(block);
        }
      }
      body.append(node('h4','','Pin and pad assignments'));
      for (const kind of ['bom','cpl']) if (c[kind]?.length) {body.append(node('h4','',kind==='bom'?'BOM evidence':'Placement evidence'));body.append(table(['Source','Value','Footprint','DNP','Layer'],c[kind].map(r=>[`${r.source_file}:${r.line}`,r.value,r.footprint,r.dnp?'Yes':'No',r.layer || '—'])));}
      const pads = c.pcb.flatMap(r=>r.pads || []), rows = [], seen = new Set();
      for (const pin of c.schematic_pins || []) {
        const matches = pads.filter(p=>p.number === pin.number); seen.add(pin.number);
        rows.push([pin.number,pin.name || '—', pin.no_connect ? 'Explicit no-connect' : pin.net ? evidenceLink(`N:${pin.net}`) : 'No net', matches.length ? [...new Set(matches.map(p=>p.net || '(no net)'))].join(', ') : 'Missing',pin.electrical_type]);
      }
      for (const pad of pads) if (!seen.has(pad.number)) rows.push([pad.number || '(unnumbered)','PCB pad','Not in exported netlist',pad.net || 'No net',pad.kind]);
      body.append(rows.length ? table(['Pin / pad','Name','Schematic net','PCB net','Type'],rows) : note('No exported pin or pad data is available. Run the KiCad checks to export connections.'));
      card.append(body);
    });
    return card;
  });
  $('component-list').replaceChildren(...(cards.length ? cards : [note('No components match this search.')]));
  if(page.more)$('component-list').append(page.more);
}
function renderNets() {
  const query = $('net-search').value.toLowerCase().trim(), p = app.project;
  const names = [...new Set([...Object.keys(p?.schematic_nets || {}),...Object.keys(p?.pcb_nets || {})])].sort(natural);
  const cards = names.filter(name => [name,...(p.schematic_nets[name] || []),...(p.pcb_nets[name] || [])].join(' ').toLowerCase().includes(query)).map(name => {
    const card = node('details','net'); card.id = `net-${name}`; const summary = node('summary');
    const schematic = p.schematic_nets[name] || [], pcb = p.pcb_nets[name] || [];
    summary.append(node('span','net-name',name),node('span','net-count',`${schematic.length} pins · ${pcb.length} pads`)); card.append(summary);
    const body = node('div','detail-body');
    for (const [label, members] of [['Schematic members',schematic],['PCB members',pcb]]) {
      body.append(node('h4','',label)); const chips = node('div','chips');
      for (const member of members) { const b = evidenceLink(`C:${member.split('.')[0]}`); b.textContent = member; chips.append(b); }
      if (!members.length) chips.append(node('span','muted','None in this run.')); body.append(chips);
    }
    card.append(body); return card;
  });
  $('net-list').replaceChildren(...(cards.length ? cards : [note('No nets match this search. If no netlist was exported, enable KiCad checks and run again.')]));
}
function renderCoverage() {
  const p = app.project, box = $('coverage-content'), grid = node('div','coverage-grid'); box.replaceChildren();
  const checks = node('div','panel'), limits = node('div','panel'); checks.append(node('h3','','Checks performed')); list(checks,(p.checks_performed || []).map(c=>c.replaceAll('_',' ')));
  if (!p.checks_performed?.length) checks.append(node('p','muted','No checks recorded.'));
  limits.append(node('h3','','What remains unverified')); list(limits,p.review?.limitations || []); grid.append(checks,limits); box.append(grid);
  const files = node('div','panel detail-body'); files.append(node('h3','','Project sources'));
  files.append(table(['File','Type','Read status'],(p.files || []).map(f=>[f.path,f.kind.replaceAll('_',' '),f.status.replaceAll('_',' ')])));
  for (const [kind, file] of Object.entries(p.selected_files || {})) files.append(node('p','basis',`Selected ${kind}: ${file}`));
  const counts = p.review?.raw_counts || {}; files.append(node('p','basis',`${counts.error || 0} errors · ${counts.warning || 0} warnings · ${counts.info || 0} information notices. Original rule classifications.`)); box.append(files);
}
function renderRaw() {
  const query = $('raw-search').value.toLowerCase().trim(), severity = $('raw-severity').value;
  const matches = (app.project?.issues || []).map((issue,i)=>({...issue,id:`F${String(i+1).padStart(4,'0')}`})).filter(i=>(severity === 'all' || severity === i.severity) && JSON.stringify(i).toLowerCase().includes(query));
  const page=paged(matches,'raw',query+severity,renderRaw);
  const cards = page.items.map(i=>{
    const card = node('details','raw-item'); card.id=i.id;
    const summary = node('summary'), main = node('div','finding-main'), badges = node('div','badges');
    badges.append(badge(i.severity,i.severity),badge(i.source)); if (i.excluded) badges.append(badge('Excluded'));
    main.append(badges,node('h3','',i.code),node('p','',i.message)); summary.append(node('span','card-id',i.id),main); card.append(summary);
    const body=node('div','detail-body'); list(body,i.evidence || []); sectionText(body,'Suggested fix',i.suggested_fix);
    if (i.component) body.append(evidenceLink(`C:${i.component}`));
    if (i.items?.length) list(body,i.items.map(item=>item.description || JSON.stringify(item)));
    if (Object.keys(i.details || {}).length) body.append(table(['Detail','Value'],Object.entries(i.details).map(([k,v])=>[k,typeof v === 'string' ? v : JSON.stringify(v)])));
    card.append(body); return card;
  });
  $('raw-list').replaceChildren(...(cards.length ? cards : [note('No original findings match this search.')]));
  if(page.more)$('raw-list').append(page.more);
}
function projectFileLabel(p){
  if(!p)return '';
  const files=[p.selected_files?.schematic,p.selected_files?.pcb].filter(Boolean);
  if(!files.length)return '';
  return files.join(' + ')+(p.workspace?.input_source?.path?' · live files':' · uploaded copies');
}
function renderProject() {
  if(app.adding)return;
  const p = app.project;
  const linkedOnly = !p && app.config && app.config.linked_files && app.config.linked_files.path;
  $('results').hidden = !p;
  // Three exclusive states: a checked project shows results; connected but
  // unchecked files show the setup form with the Run action; only a truly
  // empty workspace shows the connect onboarding. Never both at once.
  $('empty-state').hidden = !!(p || linkedOnly);
  if($('product-details'))$('product-details').hidden=!p;
  $('project-title').textContent = p ? displayProjectName(p.provenance?.project_name || p.archive) : (linkedOnly ? displayProjectName(app.config.linked_files.name || 'Demo board connected') : 'Review a KiCad design');
  const reviewed=p?.provenance?.created;
  const reviewDate=reviewed && Number.isFinite(new Date(reviewed).getTime())?new Date(reviewed).toLocaleString([], {dateStyle:'medium',timeStyle:'short'}):null;
  const fileLabel=p?projectFileLabel(p):'';
  $('project-subtitle').textContent = p ? `${fileLabel?fileLabel+' · ':''}${Object.keys(p.components || {}).length} components${reviewDate?' · Files checked '+reviewDate:''} · Save in KiCad, then update` : (linkedOnly ? ('Connected: ' + app.config.linked_files.path + ' — press “Check my board” below to analyze it.') : 'Connect the files you edit in KiCad.');
  // With no loaded report the setup form is the only path forward. Keep it in
  // the visible workspace ahead of the job panel — never stranded inside the
  // hidden results container initializeOverview files it under — and open it.
  if (!p) {
    if($('job-panel'))$('job-panel').before($('setup-disclosure'),$('source-panel'));
    if($('setup-disclosure'))$('setup-disclosure').open=true;
    return;
  }
  if($('setup-disclosure'))$('setup-disclosure').open=false;
  $('setup-summary').textContent='Connected files & check settings';
  if($('project-files-area'))$('project-files-area').append($('setup-disclosure'),$('source-panel'));
  $('quick-update').hidden=false;
  restoreInput();renderRevisionProgress();renderReadiness();renderAssembly();renderParts();loadBaselines().catch(showError);
  renderDesign();renderAssemblyDownloads();
  renderOverview();renderSchematicStatus();
  $('source-panel').open = false;
  const review=p.review || {}, groups=review.groups || [], attention=groups.filter(g=>['fix','review'].includes(g.priority)).length;
  $('result-headline').textContent = `${attention} review ${attention===1?'item':'items'} on this board`;
  $('result-explanation').textContent = review.headline || 'Review the available check results.';
  $('attention-count').textContent = attention;
  const r=p.workspace?.readiness || {};
  const metrics = [[r.open ?? attention,'Open reviews',`${r.reviewed || 0} reviewed · ${r.accepted_risk || 0} accepted risk`,'attention'],[Object.keys(p.components || {}).length,'Components','Across the selected sources',''],[p.connectivity?.pins_compared ?? '—','Pins compared',p.connectivity?.status === 'compared' ? 'Net assignments compared' : 'Connectivity not compared',''],[Object.keys(p.schematic_nets || {}).length,'Schematic nets',`${Object.keys(p.pcb_nets || {}).length} PCB nets recorded`,'']];
  $('metrics').replaceChildren(...metrics.map(([value,label,hint,kind])=>{const m=node('div',`metric ${kind}`);m.append(node('span','metric-label',label),node('strong','metric-number',value),node('span','metric-hint',hint));return m;}));
  renderSelectedDetails();
  if($('chat-view') && !$('chat-view').hidden)renderChatHome();
  if($('checks-view') && !$('checks-view').hidden)renderCheckScope();
}
function renderJob() {
  const job = app.snapshot.job;
  // A finished repair is shown in its own approval dialog. Leaving the generic
  // job banner above it made the completed investigation look like a stuck run.
  $('job-panel').hidden = !job || (job.status==='completed' && ['analysis','file_fix'].includes(job.kind));
  if (!job) return;
  $('job-panel').classList.toggle('completed',job.status==='completed');
  $('job-title').textContent = job.status === 'running' ? (job.kind === 'connection' ? 'Testing Vector' : job.kind === 'review' ? 'Reviewing saved board with Vector' : job.kind === 'rethink' ? 'Updating the change plan' : job.kind === 'question' ? 'Reviewing your question' : job.kind === 'file_fix' ? 'Researching the repair' : 'Preflight in progress') : job.status === 'failed' ? 'Could not finish' : job.kind === 'connection' ? 'API test complete' : job.kind === 'review' ? 'Vector review finished' : job.kind === 'rethink' ? 'Review updated' : job.kind === 'file_fix' ? 'Repair ready' : 'Run complete';
  if(job.status==='cancelled')$('job-title').textContent='Check stopped';
  if(job.needs_product_context)$('job-title').textContent='Waiting for your answers';
  if($('cancel-check')){$('cancel-check').hidden=job.status!=='running';$('cancel-check').disabled=!!job.cancel_requested;$('cancel-check').textContent=job.cancel_requested?'Stopping…':'Stop check';}
  $('job-panel').setAttribute?.('data-state',job.status);
  $('job-message').textContent = job.message || (job.status==='running'?'Working…':'No additional details recorded.');
  $('job-symbol').className = job.status === 'running' ? 'spinner' : job.status === 'failed' ? 'job-done job-failed' : 'job-done';
  $('job-symbol').textContent = job.status === 'running' ? '' : job.status === 'failed' ? '!' : '✓';
  $('job-time').textContent = jobElapsedText(job);
  $('job-events').replaceChildren(...(job.events || []).map(e=>node('li','',e.message)));
  if (job.kind === 'connection' && $('settings-dialog').open) {
    $('settings-message').textContent = job.message; $('settings-message').classList.toggle('error',job.status === 'failed');
  }
}
async function refresh() {
  if(app.refreshing){app.refreshAgain=true;return app.refreshing;}
  app.refreshing=(async()=>{do{app.refreshAgain=false;await refreshState();}while(app.refreshAgain);})();
  try{return await app.refreshing;}finally{app.refreshing=null;}
}
async function refreshState() {
  const [snapshot,config]=await Promise.all([api('/api/state'),api('/api/config')]);
  const previousReportId=app.snapshot?.report_id;
  const jobChanged=app.snapshot?.job?.id!==snapshot.job?.id || app.snapshot?.job?.status!==snapshot.job?.status;
  const restarted=app.config.csrf_token!==config.csrf_token;
  const connectionChanged=JSON.stringify(app.config.connection_test)!==JSON.stringify(config.connection_test) || restarted || ['key_configured','key_saved','model','provider','gemini_backup_enabled','keychain_loading'].some(key=>app.config[key]!==config[key]);
  app.config=config;app.snapshot=snapshot;
  if($('app-version')&&config.app_version)$('app-version').textContent=config.app_version;
  if(app.connectionError)clearError();
  if(restarted)app.revision=-1;
  if(connectionChanged){settingsStatus();if(app.project && !restarted)renderOverview();}
  if (app.snapshot.revision !== app.revision) {
    const project=await api('/api/project');
    if(project?.workspace && project.workspace.report_id!==snapshot.report_id){app.refreshAgain=true;return;}
    app.fixDesigns=null;
    if(snapshot.report_id){try{app.fixDesigns={report_id:snapshot.report_id,items:(await api('/api/fix-designs/'+encodeURIComponent(snapshot.report_id))).fixes || []};}catch{app.fixDesigns=null;}}
    // Polling can replace the review's DOM while someone is reading it. Keep
    // their document position across same-report refreshes; explicit navigation
    // to another report keeps its normal page-start behavior.
    const pageScroll=typeof window!=='undefined' ? window.scrollY : 0;
    const keepReadingPosition=app.view==='workspace' && previousReportId===snapshot.report_id;
    app.project=project;app.revision=snapshot.revision;renderProject();
    if(keepReadingPosition && typeof window!=='undefined' && window.scrollY!==pageScroll)
      window.scrollTo?.({top:pageScroll,behavior:'instant'});
  }else if(jobChanged && app.project && ['ai','release'].includes(app.tab))renderSelectedDetails();
  if (app.snapshot.report_id) for (const [id,file] of [['download-html','report.html'],['download-json','report.json']]) $(id).href = `/reports/${encodeURIComponent(app.snapshot.report_id)}/${file}`;
  if (app.snapshot.report_id) $('download-bundle').href=`/api/bundle/${encodeURIComponent(app.snapshot.report_id)}`;
  renderJob(); setBusy();
  if(typeof renderUsage==='function')renderUsage(app.config.usage);
  if(app.libraryNeedsRetry){await loadHistory();app.libraryNeedsRetry=false;}
  const job = app.snapshot.job;
  if (job && job.status !== 'running' && app.completedJob !== job.id) { app.completedJob = job.id; await loadHistory(); }
  if(!maybeReviewSavedProject() && !maybeAskProductQuestions())maybeShowReviewAttention();
  renderWorkflowStrip();
}
async function poll() {
  try { await refresh(); } catch(e) { showError(e); }
  setTimeout(poll,app.busy ? 1000 : 4000);
}
async function loadHistory() {
  return loadLibrary();
}
function openSettings(afterKey=null) { if($('settings-dialog').open){if(typeof afterKey==='function')app.resumeAfterKey=afterKey;return;} app.resumeAfterKey=typeof afterKey==='function'?afterKey:null; $('settings-message').textContent=''; if($('meta-api-key'))$('meta-api-key').value='';$('remember-key').checked=app.config.key_saved || !app.config.key_configured;settingsStatus(true);$('save-settings').textContent=app.resumeAfterKey?'Connect & run Vector review':'Save settings';$('settings-dialog').showModal(); }
async function saveSettings(test=false) {
  if(app.busy)return;
  let resume=null;
  app.pending=true;setBusy();$('settings-message').classList.remove('error');
  try {
    const hasNewKey=$('meta-api-key').value.trim();
    const model='meta/muse-spark-1.3-contributor';
    const credentials={meta_key:$('meta-api-key').value};
    app.config=await api('/api/settings',{model,...credentials,ai_usage_mode:'complete',remember_key:$('remember-key').checked && app.config.keychain_available!==false});
    if($('meta-api-key'))$('meta-api-key').value='';settingsStatus();if(app.project && typeof renderOverview==='function')renderOverview();
    if (hasNewKey) $('use-ai').checked=true;
    $('settings-message').textContent=app.config.key_saved?'Key saved in macOS Keychain. You won’t need to paste it after restarting.':app.config.key_configured?'API key configured for this app session.':'Settings saved. Add the Meta key to connect.';
    if (test) { await api('/api/test-ai',{});await refresh(); }
    else if(app.config.key_configured){renderStepChat();resume=app.resumeAfterKey;app.resumeAfterKey=null;$('settings-dialog').close();}
  } catch(e){if($('meta-api-key'))$('meta-api-key').value='';$('settings-message').textContent=e.message;$('settings-message').classList.add('error');}
  finally {app.pending=false;setBusy();}
  if(resume)await resume();
}
function inputMode(mode) {
  if(app.input!==mode) clearSourceSelections();
  app.input=mode;
  if($('project-path'))$('project-path').required=false;
  for (const name of ['path','upload']) { const btn=$(`mode-${name}`); if(!btn)continue; btn.classList.toggle('selected',name===mode);btn.setAttribute('aria-pressed',String(name===mode)); }
}
async function upload(file) {
  if (!file || app.busy) return;
  clearError();app.upload=null;
  if (!file.name.toLowerCase().endsWith('.zip')) {showError(new Error('Choose a KiCad project ZIP.'));return;}
  if (file.size>app.config.max_upload_bytes) {showError(new Error(`The ZIP must be smaller than ${Math.round(app.config.max_upload_bytes/1024/1024)} MB.`));return;}
  app.uploading=true;setBusy();if($('upload-label'))$('upload-label').textContent='Uploading…';
  try {
    const response=await fetch('/api/upload',{method:'POST',headers:{'X-PCB-Token':app.config.csrf_token,'X-Filename':encodeURIComponent(file.name),'Content-Type':'application/zip'},body:file});
    const result=await response.json();if (!response.ok) throw new Error(result.error || 'Upload failed.');
    result.isZip=true;inputMode('upload');app.upload=result;app.uploadFiles=[{name:result.name,size:result.size||file.size}];
    stagedFiles.clear();linkedProjectFiles.active=false;clearSourceSelections();renderUploader();
  } catch(e){showError(e);if($('upload-label'))$('upload-label').textContent='Upload failed. Drop the ZIP again.';}
  finally{app.uploading=false;setBusy();$('zip-file').value='';}
}
async function startupConfig() {
  for(;;){
    try{const config=await api('/api/config');if(app.connectionError)clearError();return config;}
    catch(error){if(!error.connectionError)throw error;showError(error);await new Promise(resolve=>setTimeout(resolve,4000));}
  }
}
async function initialize() {
  initializeSimpleSetup();
  app.config=await startupConfig(); app.serverId=app.config.server_id; $('project-path').value=app.config.default_path; $('use-ai').checked=app.config.key_configured || app.config.ai_default; $('use-native').checked=app.config.native_default; settingsStatus();
  $('dismiss-error').addEventListener('click',clearError);
  $('cancel-check').addEventListener('click',async()=>{try{await api('/api/cancel',{job_id:app.snapshot.job?.id});await refresh();}catch(e){showError(e);}});
  $('nav-chat').addEventListener('click',openChatHome);
  $('nav-checks')?.addEventListener('click',()=>{navigate('checks');renderCheckScope();});
  $('nav-workspace').addEventListener('click',()=>{if(app.project){navigate('workspace');if(app.tab==='chat')selectTab('release');}else addProject();});$('nav-history').addEventListener('click',()=>navigate('history'));
  $('nav-start')?.addEventListener('click',()=>navigate('start'));
  $('nav-livescan')?.addEventListener('click',()=>navigate('livescan'));
  $('start-checker')?.addEventListener('click',()=>{if(app.project)navigate('workspace');else navigate('history');});
  $('start-livescan')?.addEventListener('click',()=>navigate('livescan'));
  $('ai-status').addEventListener('click',openSettings);$('close-settings').addEventListener('click',()=>$('settings-dialog').close());
  $('settings-dialog').addEventListener('close',()=>{if($('meta-api-key'))$('meta-api-key').value='';app.resumeAfterKey=null;});
  $('settings-form').addEventListener('submit',e=>{e.preventDefault();saveSettings();});$('test-ai').addEventListener('click',()=>saveSettings(true));
  if($('ai-usage-mode'))$('ai-usage-mode').addEventListener('change',usageSettings);
  $('ai-model').addEventListener('change',()=>{providerSettings();$('remember-key').checked=app.config.meta_key_saved || !app.config.meta_key_configured;});
  $('forget-key').addEventListener('click',async()=>{if(app.busy)return;app.pending=true;setBusy();try{app.config=await api('/api/settings',{clear_key:true,model:'meta'});if($('meta-api-key'))$('meta-api-key').value='';$('use-ai').checked=false;settingsStatus();$('settings-message').textContent='All keys forgotten. Local checks are still available.';}catch(e){$('settings-message').textContent=e.message;}finally{app.pending=false;setBusy();}});
  $('refresh-history').addEventListener('click',async()=>{try{await api('/api/library/refresh',{});await loadHistory();}catch(e){showError(e);}});
  $('project-path').addEventListener('input',clearSourceSelections);
  $('run-form').addEventListener('submit',async e=>{
    e.preventDefault();if(app.busy)return;clearError();
    if(app.input==='path' && !$('project-path').value.trim()){showError(new Error('Select your KiCad project files first, then click Check my board.'));$('select-original-files').focus();return;}
    if (app.input==='upload' && !app.upload) {showError(new Error('Upload a ZIP before starting the preflight.'));return;}
    if($('inline-api-key').value.trim()){
      if(!await connectInlineKey())return;
    }
    if ($('use-ai').checked && !app.config.key_configured) {$('setup-disclosure').open=true;$('inline-key-entry').hidden=false;$('inline-api-key').focus();showError(new Error('Paste your Meta API key in the setup box, then click Connect Vector.'));return;}
    if ($('initial-question').value.trim() && !$('use-ai').checked) {showError(new Error('Enable Vector review to include your question for Vector, or clear the question for a local run.'));return;}
    app.adding=false;
    app.pending=true;setBusy();
    try { await api('/api/run',{context_intake:true,linked_files:app.input==='path' && app.config.linked_files?.path===$('project-path').value && app.config.linked_files?.schematic===$('source-sch').value && app.config.linked_files?.pcb===$('source-pcb').value,baseline_id:workflow.revisionBase,path:$('project-path').value,project_name:$('project-name').value.trim(),upload_id:app.input==='upload'?app.upload.id:null,ai:$('use-ai').checked,native:$('use-native').checked,datasheets:$('run-datasheets').checked,question:$('initial-question').value,pcb:$('source-pcb').value.trim(),schematic:$('source-sch').value.trim(),bom:$('source-bom').value.trim(),cpl:$('source-cpl').value.trim(),cpl_units:$('cpl-units').value});selectTab('overview');await refresh(); }
    catch(e){showError(e);}finally{app.pending=false;setBusy();}
  });
  $('ask-form').addEventListener('submit',async e=>{e.preventDefault();if(app.busy)return;clearError();if (!app.config.key_configured){openSettings();return;}app.pending=true;setBusy();try{await api('/api/chat',{report_id:app.snapshot.report_id,question:$('followup-question').value,history:[]});await refresh();}catch(e){showError(e);}finally{app.pending=false;setBusy();}});
  initializeDesign();
  initializeOverview();
  initializeReviewTabs();
  if(!SHOW_WORKFLOW_VIZ){$('workflow-toggle').hidden=true;$('workflow-shell').hidden=true;}
  else $('workflow-toggle').addEventListener('click',()=>{
    const shell=$('workflow-shell'),open=shell.classList.toggle('open');
    $('workflow-toggle').setAttribute('aria-expanded',String(open));
    $('workflow-toggle').textContent=open?'Hide live workflow':'View live workflow';
    if(open&&typeof window.refreshCommandCenter==='function')window.refreshCommandCenter();
  });
  for (const [id,render] of [['finding-search',renderFindings],['finding-category',renderFindings],['component-search',renderComponents],['net-search',renderNets],['raw-search',renderRaw],['raw-severity',renderRaw]]) $(id).addEventListener('input',render);
  initializeProjectFiles();initializeWorkflow();
  if(typeof initializeEditorConnections==='function')initializeEditorConnections();
  if(typeof initializeUsage==='function')initializeUsage();
  try{await loadHistory();}catch(error){app.libraryNeedsRetry=true;showError(error);}
  await poll();
  if(app.config.linked_files && (!app.project || app.project.workspace?.input_source?.path===app.config.linked_files.path))connectOriginalFiles(app.config.linked_files);
  if(!app.view)navigate(app.snapshot.job?.status==='running'?'workspace':'start');
  // Let the user choose when to run expensive checks. Opening the app stays interactive.

}

function initializeSimpleSetup(){
  const heading=$('history-view').querySelector('.page-heading');
  heading.querySelector('h1').textContent='Your workspace';heading.querySelector('p').textContent='Your boards. Every revision in one place.';
  heading.querySelector('.eyebrow').textContent='Project library';
  const add=node('button','primary','+ New project');add.id='add-project';add.type='button';add.addEventListener('click',addProject);heading.append(add);
  const setup=node('section','panel simple-setup');setup.setAttribute('aria-label','Project and Vector setup');
  const project=node('div','simple-project');project.append(node('h2','','1. Your KiCad project'));
  const pick=node('button','primary','Browse on this Mac…');pick.id='select-original-files';pick.type='button';
  pick.addEventListener('click',async()=>{
    if(app.busy)return;
    app.pending=true;setBusy();pick.disabled=true;
    try{
      // One press re-links the known folder with no dialog.
      if(await tryAutoLink(null))return;
      pick.textContent='Waiting for the Mac file dialog…';
      await linkViaPicker(null);
    }
    finally{app.pending=false;setBusy();pick.textContent='Browse on this Mac…';}
  });
  project.append(linkDropzone(),pick,node('p','field-help','One intake: your live KiCad files are always linked, never copied. Once your project folder is known, dropping the files links them at once with no second dialog — otherwise Browse and pick the .kicad_pro, the .kicad_pcb, and every .kicad_sch sheet together. A folder path works too.'));
  // The typed folder path is the dialog-free route (native dialogs can fail
  // to appear), so it must live in the open setup box — never buried in the
  // collapsed advanced section. Grab the reference before moving: once the
  // node is inside the still-detached setup section, getElementById cannot
  // find it again and boot would die here.
  const pathInput=$('path-input');
  if(pathInput){
    pathInput.prepend($('use-connected-project'),$('choose-local-folder'));
    project.append(pathInput);
  }
  const status=node('p','field-help','No project connected yet. Browse above or type the folder path.');status.id='connected-project-hint';project.append(status);
  const files=node('div','connected-file-list');files.id='connected-file-list';project.append(files);
  const demos=node('div');demos.id='demo-projects';project.append(demos);loadDemoProjects(demos);
  for(const hidden of hiddenProjectInputs())project.append(hidden);
  const options=document.querySelector('.run-options');options.prepend(node('h2','','2. Check your board'));
  const entry=node('div');entry.id='inline-key-entry';
  const label=node('label','','Meta API key');label.htmlFor='inline-api-key';
  const key=node('input');key.id='inline-api-key';key.type='password';key.autocomplete='off';key.placeholder='Paste your Meta API key, or open Vector settings';key.spellcheck=false;key.setAttribute('form','run-form');
  const connect=node('button','secondary','Connect Vector');connect.id='inline-connect';connect.type='button';connect.addEventListener('click',()=>connectInlineKey());
  const link=node('button','text-link','Open Vector settings');link.type='button';link.addEventListener('click',openSettings);
  const rememberLabel=node('label','check');const remember=node('input');remember.id='inline-remember-key';remember.type='checkbox';remember.checked=app.config?.keychain_available!==false;remember.disabled=app.config?.keychain_available===false;rememberLabel.append(remember,document.createTextNode('Remember key on this Mac'));entry.append(label,key,connect,rememberLabel,link);const message=node('p','field-help');message.id='inline-key-status';
  options.insertBefore(entry,$('run-button'));options.insertBefore(message,$('run-button'));
  $('run-button').setAttribute('form','run-form');options.append(node('p','field-help','After your first check, use Update to check the changes you save in KiCad.'));
  setup.append(project,options);
  const disclosure=node('details','setup-disclosure');disclosure.id='setup-disclosure';disclosure.open=true;
  const summary=node('summary','','Project files & Review settings');summary.id='setup-summary';disclosure.append(summary,setup);$('source-panel').before(disclosure);
  $('source-panel').querySelector('summary span').textContent='Other projects & advanced options';$('source-panel').open=false;
  document.querySelector('.project-name-row').hidden=true;
  $('project-path').required=false;
}
function connectOriginalFiles(linked){
  clearUploader();inputMode('path');
  $('project-path').value=linked.path;$('project-name').value=linked.name;clearSourceSelections();
  $('source-sch').value=linked.schematic;$('source-pcb').value=linked.pcb;
  $('connected-project-hint').textContent='Connected. Save in KiCad, then click Update: the app automatically reads your latest saved files. These paths stay saved on the server.';
  const list=$('connected-file-list');list.replaceChildren();
  for(const path of Object.values(linked.files || {}))list.append(node('p','field-help',path));
  for(const name of linked.sheets || [])if(name!==linked.schematic)list.append(node('p','field-help',linked.path+'/'+name));
  $('source-panel').open=false;
}
async function connectInlineKey(){
  if(app.busy)return false;
  const key=$('inline-api-key').value.trim();
  if(!key){$('inline-key-status').textContent='Paste your Meta API key first.';$('inline-api-key').focus();return false;}
  app.pending=true;setBusy();$('inline-key-status').textContent='Connecting…';
  try{app.config=await api('/api/settings',{key,model:'meta/muse-spark-1.3-contributor',remember_key:$('inline-remember-key').checked && app.config.keychain_available!==false});$('inline-api-key').value='';$('use-ai').checked=true;settingsStatus();if(app.project)renderOverview();return true;}
  catch(error){$('inline-api-key').value='';$('inline-key-status').textContent=error.message;return false;}
  finally{app.pending=false;setBusy();}
}

const linkedProjectFiles={handles:null,active:false,updateAfterPick:false};
const KICAD_SUFFIXES={'.kicad_pro':'Project','.kicad_sch':'Schematic','.kicad_pcb':'PCB','.kicad_dru':'Design rules'};
// Hidden file inputs keep dotted <input accept> suffixes, which the
// File System Access API rejects.
const REQUIRED_SUFFIXES=['.kicad_pro','.kicad_sch','.kicad_pcb'];
function suffixOf(name){const i=String(name).lastIndexOf('.');return i>=0?String(name).slice(i).toLowerCase():'';}
function formatBytes(n){if(!n)return '0 B';const u=['B','KB','MB','GB'];let v=n,i=0;while(v>=1024&&i<u.length-1){v/=1024;i++;}return `${v>=100||i===0?Math.round(v):v.toFixed(1)} ${u[i]}`;}
function typeOfFile(name){const s=suffixOf(name);if(KICAD_SUFFIXES[s])return KICAD_SUFFIXES[s];if(s==='.zip')return 'ZIP archive';if(s==='.csv')return 'Spreadsheet';return 'File';}
function committableName(name){return Object.prototype.hasOwnProperty.call(KICAD_SUFFIXES,suffixOf(name));}
// Pure checklist over staged-or-committed file names. Tested in Node.
function checklistState(names){
  const have={};for(const n of names)have[suffixOf(n)]=true;
  const missing=REQUIRED_SUFFIXES.filter(s=>!have[s]);
  return {have,missing,ready:missing.length===0,
    waitingPcb:!!(have['.kicad_pro']&&have['.kicad_sch']&&!have['.kicad_pcb']),
    optionalDru:!!have['.kicad_dru']};
}
function validateStaged(names){
  if(names.length<3)return {ok:false,error:'Add the project, schematic, and PCB files before continuing.'};
  if(names.length>50)return {ok:false,error:'Too many files. Keep the set under 50, or use a folder or ZIP.'};
  const bad=names.find(n=>!committableName(n));
  if(bad)return {ok:false,error:`${bad} cannot be uploaded directly. Add it through a folder or a ZIP.`};
  const state=checklistState(names);
  if(!state.ready)return {ok:false,error:'Missing '+state.missing.join(', ')+' file(s).'};
  return {ok:true};
}
const stagedFiles=new Map(); // name -> {file,size,status,note}
const stagedHandles=new Map(); // name -> FileSystemFileHandle
function currentFileNames(){
  if(stagedFiles.size)return [...stagedFiles.keys()];
  return (app.uploadFiles||[]).map(f=>f.name);
}
function renderUploader(){
  const list=$('staged-list'), checks=$('upload-checklist'), commit=$('commit-staged');
  if(!list||!checks||!commit)return;
  list.textContent='';
  const entries=[...stagedFiles.values()];
  const shown=entries.length?entries:(app.uploadFiles||[]).map(f=>({name:f.name,size:f.size||0,status:'ready',note:''}));
  for(const e of shown){
    const row=node('div','uz-file');
    const badge=node('span',`badge ${e.status==='ready'?'completed':e.status==='error'||e.status==='skipped'?'error':e.status==='uploading'?'warning':'review'}`,e.status==='ready'?'Ready':e.status==='uploading'?'Uploading':e.status==='error'?'Error':e.status==='skipped'?'Skipped':'Staged');
    const main=node('div','uz-file-main');
    main.append(node('strong','',e.name),node('small','',`${typeOfFile(e.name)} · ${formatBytes(e.size||0)}${e.note?' · '+e.note:''}`));
    row.append(badge,main);
    if(entries.length&&e.file){
      const remove=node('button','text-button','Remove');remove.type='button';
      remove.setAttribute('aria-label',`Remove ${e.name}`);
      remove.addEventListener('click',()=>{stagedFiles.delete(e.name);stagedHandles.delete(e.name);renderUploader();});
      row.append(remove);
    }
    list.append(row);
  }
  checks.textContent='';
  const names=currentFileNames();
  if(app.upload?.isZip){
    checks.append(node('p','field-help',`${app.upload.name} is uploaded and ready. Add KiCad files to replace it with an individual-file set.`));
  }else if(!names.length){
    checks.append(node('p','field-help','Drop your schematic, PCB, and project files above — or a KiCad ZIP, or a whole folder.'));
  }else{
    const state=checklistState(names.filter(committableName));
    for(const suffix of REQUIRED_SUFFIXES){
      const ok=state.have[suffix];
      const row=node('p',`uz-check ${ok?'uz-ok':'uz-missing'}`);
      row.textContent=`${ok?'✓':'○'} ${KICAD_SUFFIXES[suffix]} (${suffix})${ok?'':''}`;
      checks.append(row);
    }
    const dru=node('p','uz-check uz-optional');
    dru.textContent=`${state.optionalDru?'✓':'○'} Design rules (.kicad_dru) · optional`;
    checks.append(dru);
    checks.append(node('p','field-help','BOM and placement CSVs travel with a folder or ZIP.'));
    if(state.waitingPcb)checks.append(node('p','uz-waiting','Waiting for PCB file (.kicad_pcb).'));
    else if(!state.ready)checks.append(node('p','uz-waiting','Waiting for '+state.missing.join(', ')+'.'));
    else if(entries.some(e=>e.status!=='ready'))checks.append(node('p','uz-ready','Set complete. Use these files to continue.'));
  }
  const committable=entries.filter(e=>e.committable);
  const pending=committable.filter(e=>e.status!=='ready');
  const committedNames=(app.uploadFiles||[]).map(f=>f.name).sort().join(',');
  const dirty=!!(app.upload&&!app.upload.isZip&&committable.map(e=>e.name).sort().join(',')!==committedNames);
  commit.hidden=!(pending.length||dirty);
  commit.disabled=!validateStaged(committable.map(e=>e.name)).ok||app.busy;
  commit.textContent=`${dirty&&!pending.length?'Update to':'Use these'} ${committable.length} files`;
  const label=$('upload-label');
  if(label){
    if(app.upload?.isZip)label.textContent=`${app.upload.name} · ready`;
    else if(entries.length)label.textContent=`${entries.length} file${entries.length===1?'':'s'} selected`;
    else if((app.uploadFiles||[]).length)label.textContent=`${app.uploadFiles.length} files uploaded · ready`;
    else label.textContent='Drop KiCad files, a folder, or a ZIP here';
  }
}
async function addFilesToStaged(files,handles){
  if(!files.length)return;
  const zips=files.filter(f=>suffixOf(f.name)==='.zip');
  if(zips.length){
    if(files.length>1||stagedFiles.size){showError(new Error('Add a ZIP on its own. Clear the staged files first, or drop KiCad files instead.'));return;}
    await upload(zips[0]);
    return;
  }
  for(const f of files){
    const ok=committableName(f.name);
    stagedFiles.set(f.name,{file:f,name:f.name,size:f.size||0,status:ok?'staged':'skipped',
      committable:ok,note:ok?'':(suffixOf(f.name)==='.csv'?'add through a folder or ZIP':'not a KiCad project file')});
  }
  if(handles)for(const h of handles){try{stagedHandles.set(h.name,h);}catch(e){/* keep staging without handles */}}
  if(![...stagedHandles.keys()].every(n=>stagedFiles.has(n)))for(const n of [...stagedHandles.keys()])if(!stagedFiles.has(n))stagedHandles.delete(n);
  linkedProjectFiles.handles=stagedHandles.size?[...stagedHandles.values()]:null;
  renderUploader();
}
async function commitStaged(){
  const entries=[...stagedFiles.values()].filter(e=>e.committable);
  const check=validateStaged(entries.map(e=>e.name));
  if(!check.ok){showError(new Error(check.error));return;}
  if(entries.reduce((sum,e)=>sum+(e.size||0),0)>30*1024*1024){showError(new Error('Selected files exceed 30 MB. Use the local folder instead.'));return;}
  app.pending=true;setBusy();
  for(const e of entries)e.status='uploading';
  renderUploader();
  try{
    const result=await api('/api/upload-project-files',{files:await Promise.all(entries.map(async e=>({name:e.name,text:await e.file.text()})))});
    inputMode('upload');app.upload=result;app.uploadFiles=entries.map(e=>({name:e.name,size:e.size||0}));linkedProjectFiles.active=true;clearSourceSelections();
    for(const e of entries)e.status='ready';
    const pro=entries.find(e=>e.name.endsWith('.kicad_pro'));
    if(pro)$('project-name').value=pro.name.replace(/\.kicad_pro$/,'');
    const linkedStatus=$('project-files-status');
    if(linkedStatus)linkedStatus.textContent=linkedProjectFiles.handles?'Files connected. Save in KiCad, then click Update. Keep this tab open.':'Files uploaded. To check saved changes, select the files again when you click Update. For one-click updates, use the local folder path.';
  }catch(error){for(const e of entries)e.status='error';showError(error);}
  finally{app.pending=false;setBusy();renderUploader();}
}
async function updateSavedProject(){
  if(app.busy)return;workflow.revisionBase=app.snapshot.report_id;
  try{
    if(linkedProjectFiles.active && app.upload && app.input==='upload' && !app.upload.isZip){
      if(!linkedProjectFiles.handles){linkedProjectFiles.updateAfterPick=true;$('project-files').click();return;}
      const names=new Set(currentFileNames());
      const fresh=[];
      for(const handle of linkedProjectFiles.handles){const file=await handle.getFile();if(!names.size||names.has(file.name))fresh.push(file);}
      stagedFiles.clear();await addFilesToStaged(fresh,linkedProjectFiles.handles);await commitStaged();
    }else if(app.input==='upload'){const panel=$('setup-disclosure')||$('source-panel');if(panel)panel.open=true;const zip=$('zip-file');if(zip){linkedProjectFiles.updateAfterPick=true;zip.click();}return;}
    $('run-form').requestSubmit();
  }catch(error){showError(new Error('Could not read the saved files. Choose the KiCad files again. '+error.message));}
}
function clearUploader(){
  stagedFiles.clear();stagedHandles.clear();
  app.upload=null;app.uploadFiles=null;linkedProjectFiles.active=false;linkedProjectFiles.handles=null;
  renderUploader();
}
// Single intake: every project is a live link. The dropzone only stages file
// names visibly, then hands off to the same native picker + link flow as
// Browse — browsers never reveal local paths, so the picker confirm is what
// actually links. The two hidden inputs below exist only so saved
// upload-based reports can still re-read their files during Update.
function linkDropzone(){
  const dz=node('label','dropzone');dz.id='dropzone';
  dz.append(node('span','upload-icon','↑'),node('strong','','Drop KiCad files here to link the originals'));
  dz.append(node('span','','They stay on your Mac — the app links them, never copies.'));
  dz.addEventListener('dragover',e=>{e.preventDefault();if(!app.busy)dz.classList.add('dragover');});
  dz.addEventListener('dragleave',()=>dz.classList.remove('dragover'));
  dz.addEventListener('drop',async e=>{e.preventDefault();dz.classList.remove('dragover');if(app.busy)return;
    const names=[...new Set([...(e.dataTransfer?.files||[])].map(f=>f&&f.name).filter(Boolean))];
    if(!names.length)return;
    // Saved route first: no dialog when the folder is already known.
    // On failure the reason is shown with the staged files — a folder/files
    // mismatch is the usual cause and the message now names the missing names.
    if(await tryAutoLink(names))return;
    stageDroppedNames(names,tryAutoLink.lastError);});
  return dz;
}
// The browser never reveals local paths for dropped files, so a drop alone
// can never link. But once the project folder is known (an earlier link, the
// folder picker, or the typed path), dropped names resolve against that saved
// route instantly — no second dialog. Returns true when linked.
function knownLinkDir(){
  const linked=app.config&&app.config.linked_files&&app.config.linked_files.path;
  if(linked)return linked;
  const typed=$('project-path')&&$('project-path').value.trim();
  return typed||'';
}
async function loadDemoProjects(box){
  // Bundled judge-ready projects: no upload or external drive needed.
  if(!box || typeof api!=='function')return;
  let demos=[];
  try{
    const res=await api('/api/demo-projects');
    demos=(res && res.demos) || [];
  }catch(error){return;}
  if(!demos.length)return;
  box.append(node('p','field-help','No upload needed — open a bundled demo (a disposable working copy; originals stay pristine):'));
  for(const demo of demos){
    const btn=node('button','secondary','Open “'+displayProjectName(demo.name)+'”');btn.type='button';
    btn.addEventListener('click',async()=>{
      if(app.busy){showError(new Error('A check is already running. Wait for it to finish before opening another board.'));return;}
      btn.disabled=true;
      try{const linked=await api('/api/demo-open',{name:demo.name});app.config.linked_files=linked;connectOriginalFiles(linked);app.project=null;app.fixDesigns=null;workflow.comparison=null;app.adding=false;app.revision=-1;navigate('workspace');await refresh();}
      catch(e){showError(e);}
      finally{btn.disabled=false;}
    });
    box.append(btn);
  }
}
async function tryAutoLink(names,dirOverride){
  tryAutoLink.lastError='';
  const directory=dirOverride||knownLinkDir();
  if(!directory){tryAutoLink.lastError='Type the project folder path first, then drop the files.';return false;}
  try{
    const linked=await api('/api/link-local-paths',{directory,names:names||null});
    if(!linked||linked.cancelled)return false;
    app.config.linked_files=linked;connectOriginalFiles(linked);
    return true;
  }catch(error){tryAutoLink.lastError=error&&error.message||String(error);return false;}
}
function stageDroppedNames(names,autoError){
  names=[...new Set((names||[]).filter(Boolean))];
  if(!names.length)return null;
  const list=$('connected-file-list');
  if(list){list.replaceChildren();for(const name of names)list.append(node('p','field-help',name+' · waiting to link'));}
  const hint=$('connected-project-hint');
  if(hint)hint.textContent=(autoError?('Automatic linking did not work: '+autoError+' '):'')+'These files are staged below. Press “Link these files” — with your project folder already known it links at once, otherwise pick the same set in the Mac file dialog to finish linking.';
  const link=node('button','primary','Link these '+names.length+' files');link.type='button';link.id='link-staged-files';
  link.addEventListener('click',async()=>{
    if(app.busy)return;
    link.disabled=true;link.textContent='Linking…';
    try{
      // Saved route first: with the folder already known this finishes with
      // no dialog at all. The picker is only the fallback.
      if(await tryAutoLink(names))return;
      link.textContent='Waiting for the Mac file dialog…';
      await linkViaPicker(names);
    }
    finally{if(link.isConnected){link.disabled=false;link.textContent='Link these '+names.length+' files';}}
  });
  if(list)list.append(link);
  return link;
}
async function linkViaPicker(expected){
  try{
    const params={};
    const last=app.config&&app.config.linked_files&&app.config.linked_files.path;
    if(last)params.directory=last;
    const linked=await api('/api/choose-local-files',params);
    if(linked.cancelled)return false;
    app.config.linked_files=linked;connectOriginalFiles(linked);
    const linkedNames=new Set([...Object.values(linked.files||{}).map(p=>String(p).split('/').pop()),...(linked.sheets||[])].map(s=>String(s).toLowerCase()));
    const missing=(expected||[]).filter(n=>!linkedNames.has(String(n).toLowerCase()));
    if(missing.length)showError(new Error('Linked, but these files were not in the linked set: '+missing.join(', ')+'. The list shows the actual linked files.'));
    return true;
  }catch(error){showError(error);return false;}
}
function hiddenProjectInputs(){
  const zip=node('input');zip.id='zip-file';zip.type='file';zip.multiple=true;zip.hidden=true;
  zip.setAttribute('accept','.zip,.kicad_pro,.kicad_sch,.kicad_pcb,.kicad_dru');
  zip.addEventListener('change',async e=>{try{await addFilesToStaged([...e.target.files]);if(linkedProjectFiles.updateAfterPick){linkedProjectFiles.updateAfterPick=false;await commitStaged();$('run-form').requestSubmit();}}catch(err){showError(err);}finally{e.target.value='';}});
  const input=node('input');input.id='project-files';input.type='file';input.multiple=true;input.hidden=true;
  input.setAttribute('accept','.kicad_pro,.kicad_sch,.kicad_pcb,.kicad_dru,.csv');
  return [zip,input];
}
function initializeProjectFiles(){
  function connectFolder(path,name){
    clearUploader();inputMode('path');
    $('project-path').value=path;$('project-name').value=name;clearSourceSelections();
    const folderStatus=$('project-files-status');
    if(folderStatus)folderStatus.textContent='Connected to '+path+'. Save in KiCad, then click Update. Your saved files are read directly each time.';
    $('connected-project-hint').textContent=name+' connected. Save in KiCad → click Update.';
  }
  $('use-connected-project').hidden=!app.config.linked_files;
  $('use-connected-project').addEventListener('click',()=>{if(app.config.linked_files)connectOriginalFiles(app.config.linked_files);});
  $('choose-local-folder').addEventListener('click',async()=>{
    if(app.busy)return;
    app.pending=true;setBusy();
    try{
      const folder=await api('/api/choose-local-folder',{});
      if(folder.cancelled)return;
      // The picked folder is a confirmed route: link its files at once.
      // Ambiguous folders (several projects) keep the typed-path fallback.
      if(await tryAutoLink(null,folder.path))return;
      connectFolder(folder.path,folder.name);
    }catch(error){showError(error);}finally{app.pending=false;setBusy();}
  });
  $('project-files').addEventListener('change',async event=>{
    try{linkedProjectFiles.handles=null;stagedHandles.clear();await addFilesToStaged([...event.target.files]);
      if(linkedProjectFiles.updateAfterPick){linkedProjectFiles.updateAfterPick=false;await commitStaged();$('run-form').requestSubmit();}}
    catch(error){showError(error);}finally{event.target.value='';}
  });
  const details=$('folder-details');
  if(details&&$('path-input'))details.append($('path-input'));
}

// Start only after every top-level declaration above has run: the sync
// init path renders the uploader, which reads module-level staged state.
initialize().catch(showError);
