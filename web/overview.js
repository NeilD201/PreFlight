'use strict';

const overviewUI = {section:'connections', side:'front', focus:null, view:'schematic', proof:new Map(), loading:new Set(), schematicLoading:false, sheetBox:null, sheetFocus:null, search:'', target:0, showAll:false};
const primaryViews = ['release','connections','board','manufacturing','assembly','project'];
// Preserve saved report categories while presenting one physical-board workspace.
function reviewSection(section){return ['placement','traces'].includes(section)?'board':section;}

// Whether a follow-up question may be shown to the current designer.
// The server annotates every displayed question with the creator's own
// beginner/expert selection; unannotated reports keep prior behavior.
// Beginners decide product behavior, never electrical implementation.
function questionPosed(check, question){
  const pose=check?.missing_context_pose?.[question];
  if(!pose)return true;
  const beginner=typeof app==='undefined' || app.project?.explanation_profile?.mode==='plain';
  return beginner ? pose.ask_beginner!==false : pose.ask_expert!==false;
}

// Action steps built verbatim from a follow-up question (advice.py copies
// missing_context into steps) carry the same selection via the originating
// check's pose map. Pure instructions have no pose entry and always show.
function stepPosed(action, step){
  for(const check of action?.pin_checks || []){
    const pose=check.missing_context_pose?.[step];
    if(pose)return questionPosed(check, step);
  }
  return true;
}

// Follow-up questions suppressed for the current designer, from every pose
// map on the group and its pin checks. The Analysis paragraph bakes them
// into "Still needed: ..." prose, so the filter below needs this list.
function suppressedQuestions(group, action){
  const out=[];
  const beginner=typeof app==='undefined' || app.project?.explanation_profile?.mode==='plain';
  const take=pose=>{for(const [question,p] of Object.entries(pose || {})){
    if(beginner ? p.ask_beginner===false : p.ask_expert===false)out.push(question);}};
  take(group?.missing_context_pose);
  for(const check of action?.pin_checks || [])take(check.missing_context_pose);
  return out;
}

// Remove "Still needed: ..." segments that restate a suppressed question.
// The bake joins verbatim questions with "; ", so exact normalized matches
// hit; the length guard keeps a short phrase from eating an instruction.
function filterStillNeeded(text, suppressed){
  const value=String(text || '');
  const marker='Still needed:';
  const at=value.indexOf(marker);
  if(at<0 || !suppressed.length)return value;
  const norm=s=>String(s).toLowerCase().replace(/\s+/g,' ').trim();
  const head=value.slice(0,at).trimEnd();
  const segs=value.slice(at+marker.length).split(';').map(s=>s.trim()).filter(Boolean);
  const kept=segs.filter(seg=>!suppressed.some(q=>{
    const a=norm(seg),b=norm(q);
    return a && b && Math.min(a.length,b.length)>=24 && (a.includes(b) || b.includes(a));}));
  if(kept.length===segs.length)return value;
  if(!kept.length)return head;
  return head+' '+marker+' '+kept.join('; ');
}

// Historical reports retain their original text; navigation hints use today's menus.
function reviewInstruction(text){
  let result=String(text || '');
  for(const [oldLabel,label] of [['Investigate and design a fix','Prepare my fix'],['Open Connections','Open Circuit'],['Fabrication →','Manufacturer →'],['Before ordering','Overview'],['Placement & PCB','PCB layout'],['Traces & signals','Signals'],['More →','Inspect →'],['“Parts & datasheets”','“Inspect → Datasheets”']])result=result.replaceAll(oldLabel,label);
  return explainIssueText(result.replace(/Is GPIO1 intended to be used as an externally driven temperature-conversion trigger\?/gi, 'Should another device tell this temperature sensor when to take a reading by sending a signal to GPIO1?'));
}

function componentDescriptions(){return typeof app==='undefined'?{}:(app.project?.component_descriptions || {});}

function withComponentContext(text,form='full',seen=null){
  const value=String(text || '');if(!value)return value;
  const descs=componentDescriptions(),keys=Object.keys(descs);
  if(!keys.length)return value;
  const firsts=seen || new Set();
  // Keep quoted evidence, net identifiers and already-parenthesized refs intact.
  return value.split(/(`[^`]*`|"[^"]*"|“[^”]*”|\[[^\]]*\]|\([^)]*\)|\bNet-\S+)/g).map((part,index)=>index%2?part:part.replace(/\b([A-Za-z]{1,3}\d{1,4})\b/g,(match)=>{
    const described=descs[match];if(!described)return match;
    const repeat=firsts.has(match);firsts.add(match);
    if(repeat)return described.short;
    return form==='short'?described.short:described.phrase;
  })).join('');
}

function explainIssueText(text){
  const value=String(text || '');
  if(app.project?.explanation_profile?.mode!=='plain')return withComponentContext(value);
  const meanings={
    'exposed pad':'metal pad underneath the chip',
    'courtyard':'space reserved around a component',
    'annular ring':'copper ring around a drilled hole',
    'pull-up resistor':'resistor that holds a signal high when nothing drives it low',
    'return path':'route the current takes back to its source',
    'decoupling capacitor':'nearby capacitor that helps keep a chip’s power steady',
    'solder mask':'protective coating over the copper',
    'via':'plated hole that connects copper layers',
    'clearance':'gap between copper features',
    'crosstalk':'unwanted signal coupling between nearby connections',
  };
  let count=0;const seen=new Set();
  // Keep quoted evidence, net identifiers and existing explanations intact.
  const simplified=value.split(/(`[^`]*`|"[^"]*"|“[^”]*”|\[[^\]]*\]|\([^)]*\)|\bNet-\S+)/g).map((part,index,parts)=>index%2?part:part.replace(/\b(exposed pad|courtyard|annular ring|pull-up resistor|return path|decoupling capacitor|solder mask|via|clearance|crosstalk)\b(?!\s*\()/gi,(word,match,offset)=>{
    if(!part.slice(offset+word.length).trim() && parts[index+1]?.startsWith('('))return word;
    const key=word.toLowerCase();if(seen.has(key)||count>=2)return word;
    seen.add(key);count++;return `${word} (${meanings[key]})`;
  })).join('');
  return withComponentContext(simplified);
}

// A short orientation for saved pin reviews; the complete original reasoning
// remains in Analysis & pin checks, with all references and qualifications.
function issueObservation(group,action){
  const original=action?.footprint_context?.summary || action?.advice_summary || action?.summary || group.summary;
  if(app.project?.explanation_profile?.mode!=='plain' || !action?.pin_checks?.length)return original;
  const checks=action.pin_checks,changed=checks.filter(p=>p.decision==='change'),missing=checks.filter(p=>p.decision==='needs_context');
  const names=items=>[...new Set(items.map(p=>p.reference))].join(', ');
  const parts=[`Preflight compared ${checks.length} connections on ${names(checks)} with the available part information.`];
  if(changed.length)parts.push(`The analysis proposes changing connections on ${names(changed)}. See the action below.`);
  if(missing.length)parts.push(`An answer is still needed for ${names(missing)} before deciding whether those connections should change.`);
  if(!changed.length && !missing.length)parts.push('No wiring change is proposed for these connections.');
  if(action.disposition==='investigate')parts.push('This conclusion is conditional; the assumptions are listed under Analysis & pin checks.');
  return parts.join(' ');
}

function applyExplanationProfile(profile){
  if(!app.project || !profile)return;
  const changed=JSON.stringify(app.project.explanation_profile)!==JSON.stringify(profile);
  app.project.explanation_profile=profile;
  if(!changed)return;
  // Keep the shared chat node, draft and selected issue while refreshing prose.
  if(['connections','board','manufacturing','notes','overview'].includes(app.tab) && $('overview-detail'))renderOverviewIssues();
  if(app.tab==='release')renderRelease();
}

function initializeOverview() {
  const tabs=document.querySelector('[role=tablist]');
  for(const [id,label] of [['board','PCB & signals'],['connections','Circuit']]){
    const button=node('button');button.id='tab-'+id;button.dataset.tab=id;button.setAttribute('role','tab');button.setAttribute('aria-controls','panel-overview');button.append(node('span','',label));const count=node('span','tab-count');count.id='count-'+id;button.append(count);tabs.prepend(button);
  }
  initializeManufacturing(tabs);
  initializeRelease(tabs);
  const project=node('button','','Project');project.id='tab-project';project.dataset.tab='project';project.setAttribute('role','tab');project.setAttribute('aria-controls','panel-project');tabs.append(project);
  const panel=node('section');panel.id='panel-project';panel.hidden=true;panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby','tab-project');$('results').append(panel);
  $('tab-assembly').textContent='Parts & assembly';
  const extra=node('select','more-view');extra.id='more-view';extra.setAttribute('aria-label','More tools');
  for(const [value,label] of [['','Inspect'],['notes','KiCad & library notes'],['datasheets','Datasheets'],['actions','Action list'],['findings','Original findings'],['changes','Revision comparison'],['ai','Review log'],['components','Components'],['nets','Net connections'],['coverage','Check coverage']]){
    const option=node('option','',label);option.value=value;extra.append(option);
  }
  extra.addEventListener('change',()=>{if(extra.value)selectTab(extra.value);});
  for(const button of tabs.querySelectorAll('[data-tab]'))if(!primaryViews.includes(button.dataset.tab))button.classList.add('advanced-tab');
  tabs.append(extra);
  const results=$('results');results.prepend(tabs);
  const files=node('section','project-files-area');files.id='project-files-area';files.append(node('h2','','Project files'),$('setup-disclosure'),$('source-panel'));
  panel.append($('design-panel'),files);
  for(const [label,items] of [['Changes since the last run',[$('revision-progress')]],['Check coverage',[$('schematic-status'),results.querySelector('.coverage-disclosure')]],['Download this review',[results.querySelector('.result-top')]]]){
    const section=node('details','project-disclosure');section.append(node('summary','',label),...items);panel.append(section);
  }
  $('panel-assembly').prepend($('assembly-downloads'));
  const update=node('button','primary','Update review');update.id='quick-update';update.type='button';update.title='Save in KiCad first. Reads the connected files and compares this revision.';
  update.addEventListener('click',updateSavedProject);
  $('workspace-view').querySelector('.page-heading').append(update);
  const firmware=node('button','secondary','Firmware');firmware.id='quick-firmware';firmware.type='button';firmware.title='Discover the MCU in this review and plan firmware. KiCad files are never changed.';
  firmware.addEventListener('click',()=>{if(typeof openFirmwareDiscovery==='function')openFirmwareDiscovery().catch(showError);});
  $('workspace-view').querySelector('.page-heading').append(firmware);
  const product=node('button','secondary','Product details');product.id='product-details';product.type='button';product.hidden=true;
  product.addEventListener('click',()=>editProductContext().catch(showError));
  $('workspace-view').querySelector('.page-heading').append(product);
  selectTab('release');
}

function boardPoint(record, view) {
  if(!record || !Number.isFinite(record.x_mm) || !Number.isFinite(record.y_mm))return null;
  const [left,top,width,height]=view.bounds;
  const [ox,oy]=view.offset;
  const x=((view.mirrored?ox-record.x_mm:ox+record.x_mm)-left)/width;
  const y=(oy+record.y_mm-top)/height;
  return x>=0 && x<=1 && y>=0 && y<=1 ? [x,y] : null;
}

function selectedSignalPath() {
  if(overviewUI.chainView){const chain=viewedRoutingChain();if(chain)return {id:chain.id,label:chain.title,nets:chain.nets.map(net=>app.project.signal_paths?.net_geometry?.[net]).filter(Boolean)};}
  const group=(app.project?.review?.groups || []).find(item=>item.id===overviewUI.focus);
  return (app.project?.signal_paths?.paths || []).find(path=>path.id===group?.signal_path_id);
}

function routeOverlay(path, view, side, focusBounds=null) {
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.classList.add(focusBounds?'route-zoom-svg':'route-overlay');
  const [left,top,width,height]=view.bounds,[ox,oy]=view.offset;
  svg.setAttribute('viewBox',(focusBounds || view.bounds).join(' '));
  svg.setAttribute('aria-hidden','true');
  if(focusBounds){
    const image=document.createElementNS('http://www.w3.org/2000/svg','image');
    image.setAttribute('href',`/reports/${encodeURIComponent(app.snapshot.report_id)}/${view.file}`);
    for(const [key,value] of Object.entries({x:left,y:top,width,height}))image.setAttribute(key,value);
    svg.append(image);
  }
  for(const net of path.nets || [])for(const segment of net.geometry || []){
    const layer=segment.layer || '';
    if(!layer.startsWith(side==='front'?'F.':'B.') && !layer.startsWith('In'))continue;
    const line=document.createElementNS('http://www.w3.org/2000/svg','line');
    line.setAttribute('x1',view.mirrored?ox-segment.start[0]:ox+segment.start[0]);
    line.setAttribute('y1',oy+segment.start[1]);
    line.setAttribute('x2',view.mirrored?ox-segment.end[0]:ox+segment.end[0]);
    line.setAttribute('y2',oy+segment.end[1]);
    line.setAttribute('stroke-width',Math.max(.32,(segment.width_mm || .15)*2.5));
    if(layer.startsWith('In'))line.setAttribute('stroke-dasharray','.6 .4');
    svg.append(line);
  }
  return svg;
}

function routeBounds(path,view,side){
  const [ox,oy]=view.offset, points=[];
  for(const net of path.nets || [])for(const s of net.geometry || []){
    if(!s.layer?.startsWith(side==='front'?'F.':'B.') && !s.layer?.startsWith('In'))continue;
    for(const point of [s.start,s.end])points.push([view.mirrored?ox-point[0]:ox+point[0],oy+point[1]]);
  }
  if(!points.length)return null;
  const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
  const left=Math.min(...xs),top=Math.min(...ys),width=Math.max(5,Math.max(...xs)-left+4),height=Math.max(5,Math.max(...ys)-top+4);
  return [left-2,top-2,width,height];
}

function overviewActions() {
  const p=app.project;
  const actions=new Map((p.action_plan?.actions || []).map(action=>[action.group_id,action]));
  return (p.review?.groups || []).filter(group=>reviewSection(p.review_sections?.assignments?.[group.id]?.section || 'notes')===overviewUI.section)
    .filter(group=>overviewUI.section==='notes' || overviewUI.showAll || p.review_sections?.assignments?.[group.id]?.attention)
    .map(group=>({group,action:actions.get(group.id)}));
}

function findingLabel(group,action){
  const assignment=typeof app==='undefined'?null:app.project?.review_sections?.assignments?.[group.id];
  if(action?.manufacturer_check)return {supported:'Within process limits',option:'Order requirement',outside:'Outside process limits',unknown:'Process review'}[action.manufacturer_check.status] || 'Process review';
  if(assignment?.status==='accepted')return action?.pin_checks?.length?'Connections match supplied requirements':'No change required';
  if(action?.reference_only)return 'Footprint note';
  if(action?.disposition==='no_change')return 'No change required';
  if(assignment?.status==='reference' || assignment?.section==='notes')return 'Reference note';
  if(assignment?.status==='context')return action?.pin_checks?.some(p=>p.decision==='needs_context')?'Answer needed':'Analysis incomplete';
  return group.priority==='fix'?'Change required':group.priority==='note'?'Reference':action?.disposition==='change'?'Change proposed':'Finding';
}

function outcomeLabel(group){
  return {'verified_correction':'Verified correction ready','conditional':'Conditional correction','investigation_required':'Needs investigation','informational':'Informational'}[group.outcome] || group.outcome;
}
function issueTitle(group,action){return withComponentContext(issueTitleRaw(group,action),'short');}
function issueTitleRaw(group,action){
  if(action?.manufacturer_check && action.title)return action.title;
  if(action?.advice_title)return action.advice_title;
  if(action?.footprint_context)return `${group.components.join(', ')} · ${action.footprint_context.title}`;
  if(action?.pin_checks?.length && !pinAnalysisPending(group,action))return action.pin_checks.some(p=>p.decision==='change')?'Pin connections: changes identified':action.pin_checks.some(p=>p.decision==='needs_context')?'Pin connections: one detail needed':'Pin connections compared';
  const title=group.title;
  const names={'Pin-type conflicts need operating-mode context':'Pin connections: analysis incomplete','Via dimensions versus saved limits':'Via dimensions','Lib footprint mismatch':'Footprint differs from library','Lib symbol mismatch':'Symbol differs from library','Mirrored text on front layer':'Front-layer text is mirrored'};
  if(names[title])return names[title];
  return title.replace(/^Copper spacing versus saved clearance: (.+)$/,'$1 · Copper spacing')
    .replace(/^Shared solder-mask openings: (.+)$/,'$1 · Shared mask opening')
    .replace(/^Antenna feed has no identified endpoint: (.+)$/,'$1 · Antenna endpoint unknown');
}

function issueSteps(value){
  return (Array.isArray(value)?value:[]).filter(step=>typeof step==='string' && step.trim().length>1).map(step=>step.trim());
}

function pinAnalysisPending(group,action){
  return (app.project.review_sections?.pending_erc_ids || []).includes(group.id) ||
    (group.issue_ids || []).some(fid=>/^F\d+$/.test(fid) && app.project.issues?.[Number(fid.slice(1))-1]?.code==='KICAD_ERC_PIN_TO_PIN') && !action?.pin_checks?.length;
}

function pendingPinGroupCount(){
  const actions=new Map((app.project.action_plan?.actions || []).map(a=>[a.group_id,a]));
  return (app.project.review?.groups || []).filter(g=>pinAnalysisPending(g,actions.get(g.id))).length;
}

function comparedPins(){
  const pins=new Map();
  for(const action of app.project.action_plan?.actions || [])for(const check of action.pin_checks || []){
    const key=JSON.stringify([check.reference,check.pin,check.net]);
    // Prefer the unresolved/change decision if overlapping groups ever disagree.
    if(!pins.has(key) || pins.get(key).decision==='keep')pins.set(key,check);
  }
  return [...pins.values()];
}

// Groups the current review still wants the designer to look at, across every section.
function reviewAttentionGroups(){
  const project=app.project;
  return (project.review?.groups || []).filter(group=>project.review_sections?.assignments?.[group.id]?.attention);
}

// Why the Vector circuit review cannot be counted as complete, or 'running' while it may still finish.
function vectorIncompleteReason(){
  const ai=app.project.ai_review || {};
  if(ai.status==='completed')return null;
  if(app.busy || ai.status==='running' || ai.status==='preparing')return 'running';
  return {failed:'The Vector review failed for this version.',stale:'The saved Vector review is out of date.',needs_context:'The Vector review is waiting on product questions.',blocked:'The Vector review is blocked for this version.',cancelled:'The Vector review was cancelled.',unavailable:'The Vector review is unavailable for this version.',not_configured:'Vector is not connected, so the circuit review has not run.',disabled:'Vector review is disabled, so the circuit review has not run.',empty:'The Vector review produced no result for this version.'}[ai.status] || 'The Vector review has not completed for this version.';
}

// Whole-review empty-state decision: zero findings only counts when every check actually finished.
function reviewCompletionState(){
  const vector=vectorIncompleteReason();
  if(vector==='running')return {kind:'running'};
  const gaps=[];
  if(vector)gaps.push(vector);
  if(app.project.review?.coverage_incomplete)gaps.push('Some checks were skipped or could not be verified for this version.');
  const pending=pendingPinGroupCount();
  if(pending)gaps.push(`${pending} pin ${pending===1?'group still needs':'groups still need'} a supported interpretation.`);
  if(reviewAttentionGroups().length)return {kind:'attention',gaps};
  if(gaps.length)return {kind:'gaps',gaps};
  return {kind:'clean'};
}

function openOverviewIssue(id) {
  const destination=app.project.review_sections?.assignments?.[id]?.section;
  if(destination==='manufacturing' && app.tab!=='manufacturing')selectTab('manufacturing');
  else if(destination!=='manufacturing' && app.tab==='manufacturing')selectTab(destination || 'notes');
  overviewUI.chainView=false;overviewUI.chainPin=null;
  overviewUI.focus=id;
  overviewUI.preferredPin=null;
  const raw=(app.project.review?.groups || []).find(g=>g.id===id);
  const oldSection=overviewUI.section;
  overviewUI.section=reviewSection(app.project.review_sections?.assignments?.[id]?.section || 'notes');
  const action=app.project.action_plan?.actions?.find(a=>a.group_id===id);
  if(raw && (!['fix','review'].includes(raw.priority) || action?.disposition==='no_change'))overviewUI.showAll=true;
  const match=overviewActions().find(item=>item.group.id===id);
  if(match){
    overviewUI.view=action?.footprint_context || ['board','manufacturing'].includes(overviewUI.section)?'pcb':'schematic';
    const ref=(match.group.components || []).find(name=>app.project.components?.[name]?.pcb?.length);
    const layer=ref && app.project.components[ref].pcb[0].layer;
    if(layer?.startsWith('B.'))overviewUI.side='back';
    if(layer?.startsWith('F.'))overviewUI.side='front';
  }
  overviewUI.sheetFocus=null;
  overviewUI.target=0;
  if(oldSection!==overviewUI.section)renderOverview();else renderOverviewIssues();
}

function renderOverviewMap() {
  const p=app.project, box=$('overview-map');if(!box)return;
  overviewUI.locationObserver?.disconnect();
  box.replaceChildren();
  const visual=p.workspace?.board_visual || {};
  const locationGroup=routingViewerGroup(currentViewerGroup());
  const sites=boardSites(locationGroup,p);
  if(sites.length && overviewUI.boardSiteScope!==app.snapshot.report_id+':'+locationGroup.id)overviewUI.side=preferredBoardSide(sites[0],overviewUI.side);
  if(sites.length && overviewUI.preferredPin){const site=sites.find(s=>s.items.some(p=>p.reference+':'+p.pin===overviewUI.preferredPin));if(site)overviewUI.side=preferredBoardSide(site,overviewUI.side);}
  const top=node('div','board-heading');
  const heading=node('div');heading.append(node('span','kicker','BOARD VIEW'),node('h2','','Find it on the PCB'),node('p','','Select a marker to see the matching issue.'));
  top.append(heading);
  const sides=node('div','side-switch');
  for(const side of ['front','back']){
    const button=node('button',overviewUI.side===side?'selected':'',side==='front'?'Front':'Back');button.type='button';button.setAttribute('aria-pressed',String(overviewUI.side===side));
    button.addEventListener('click',()=>{overviewUI.side=side;renderOverviewMap();});sides.append(button);
  }
  top.append(sides);box.append(top);
  if(visual.status!=='ready' || !visual.views?.[overviewUI.side]){
    const fallback=node('div','map-unavailable');fallback.append(node('strong','','Board image unavailable'),node('p','',visual.message || 'Run a new check to create a KiCad board preview. The issue list is still available.'));
    box.append(fallback);return;
  }
  if(sites.length){renderBoardLocation(box,locationGroup,visual,sites);return;}
  box.append(node('p','field-help','Component positions are shown for context. This finding has no resolved pad or trace location.'));
  const view=visual.views[overviewUI.side], stage=node('div','board-stage');
  stage.style.aspectRatio=`${view.bounds[2]} / ${view.bounds[3]}`;
  const image=node('img','board-image');image.src=`/reports/${encodeURIComponent(app.snapshot.report_id)}/${view.file}`;
  image.alt=`KiCad ${overviewUI.side} copper, silkscreen and board outline`;
  stage.append(image);
  const path=selectedSignalPath();
  if(path)stage.append(routeOverlay(path,view,overviewUI.side));
  const markers=new Map();
  const baseGroup=currentViewerGroup();
  const group=baseGroup && routingViewerGroup(baseGroup);
  for(const ref of group?.components || []){
    const record=p.components?.[ref]?.pcb?.[0];
    if(!record || (record.layer?.startsWith('B.')?'back':'front')!==overviewUI.side)continue;
    const point=boardPoint(record,view);if(!point)continue;
    if(!markers.has(ref))markers.set(ref,{point,groupIds:[]});
    markers.get(ref).groupIds.push(overviewUI.focus);
  }
  for(const [ref,mark] of markers){
    const button=node('button',`map-pin${mark.groupIds.includes(overviewUI.focus)?' active':''}`,ref);
    button.type='button';button.style.left=`${mark.point[0]*100}%`;button.style.top=`${mark.point[1]*100}%`;
    button.title=`${ref}: ${mark.groupIds.length} review ${mark.groupIds.length===1?'item':'items'}`;
    button.setAttribute('aria-label',button.title);
    button.addEventListener('click',()=>openOverviewIssue(mark.groupIds[0]));stage.append(button);
  }
  const nativeTargets=pcbTargets(group,p,overviewUI.side);
  if(nativeTargets.length){
    if(overviewUI.target>=nativeTargets.length)overviewUI.target=0;
    const selected=nativeTargets[overviewUI.target];
    const picker=node('select');picker.setAttribute('aria-label','PCB evidence location');
    for(const [index,target] of nativeTargets.entries()){const option=node('option','',target.label);option.value=String(index);picker.append(option);}
    picker.value=String(overviewUI.target);picker.addEventListener('change',()=>{overviewUI.target=Number(picker.value);renderOverviewMap();});box.append(picker);
    const [ox,oy]=view.offset,x=view.mirrored?ox-selected.point[0]:ox+selected.point[0],y=oy+selected.point[1];
    const svg=routeOverlay({nets:[]},view,overviewUI.side,[x-2,y-2,4,4]);svg.classList.add('pcb-evidence-closeup');
    for(const target of nativeTargets){const ring=document.createElementNS(svg.namespaceURI,'circle');ring.setAttribute('cx',view.mirrored?ox-target.point[0]:ox+target.point[0]);ring.setAttribute('cy',oy+target.point[1]);ring.setAttribute('r','.16');ring.setAttribute('class','schematic-focus');ring.setAttribute('vector-effect','non-scaling-stroke');const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=target.label;ring.append(title);svg.append(ring);}
    box.append(svg,node('p','field-help','Orange rings mark the items named in the KiCad finding. '+selected.label));
    const whole=node('details');whole.append(node('summary','','Show whole board'),stage);box.append(whole);
  }else box.append(stage);
  if(path){
    const bounds=routeBounds(path,view,overviewUI.side);
    if(bounds){const zoom=node('div','route-zoom');zoom.append(node('strong','','Close-up of highlighted copper'),routeOverlay(path,view,overviewUI.side,bounds));box.append(zoom);}
  }
  const foot=node('div','board-foot');foot.append(node('span','',path?`Gold = ${path.label} copper · dashed = inner layer`:`${markers.size} marked components on the ${overviewUI.side}`),node('span','',path?'Select another item to see its location':'Board-wide checks appear in the list →'));box.append(foot);
}

function pcbTargets(group,project,side){
  const seen=new Set(),targets=[];
  for(const fid of group?.issue_ids || []){
    const issue=project.issues?.[/^F\d+$/.test(fid)?Number(fid.slice(1))-1:-1];
    if(issue?.source!=='kicad_drc')continue;
    for(const item of issue.items || []){
      if(!Number.isFinite(item.pos?.x) || !Number.isFinite(item.pos?.y))continue;
      const description=item.description || '',layer=description.match(/\bon ([FB])\./)?.[1];
      if(layer && layer!==(side==='front'?'F':'B'))continue;
      const key=item.uuid || `${item.pos.x},${item.pos.y}:${description}`;
      if(seen.has(key))continue;seen.add(key);targets.push({point:[item.pos.x,item.pos.y],label:description});
    }
  }
  return targets;
}

function renderOverviewIssues(listOnly=false) {
  const all=overviewActions(),box=$('overview-issues'),detail=$('overview-detail');if(!box || !detail)return;
  const scrollTop=box.querySelector('.overview-issue-scroll')?.scrollTop || 0;
  const items=all.filter(({group})=>!overviewUI.search || `${group.title} ${group.components.join(' ')}`.toLowerCase().includes(overviewUI.search.toLowerCase()));
  if(!all.some(item=>item.group.id===overviewUI.focus)){overviewUI.focus=all[0]?.group.id || null;listOnly=false;}
  // Move the shared chat before clearing its current parent.
  const chat=$('step-chat');if(!listOnly && chat)$('panel-actions').append(chat);
  box.replaceChildren();if(!listOnly)detail.replaceChildren();
  if(typeof renderProjectContextBox==='function' && app.snapshot && app.snapshot.report_id && !listOnly){
    const typing=typeof document!=='undefined' && document.activeElement && document.activeElement.getAttribute
      && document.activeElement.getAttribute('aria-label')==='Project context';
    const top=node('div');box.append(top);
    renderProjectContextBox(top,app.snapshot.report_id).then(area=>{
      if(typing && area && area.focus){area.focus();try{area.setSelectionRange(area.value.length,area.value.length);}catch{}}
    }).catch(()=>{});
  }
  const head=node('div','issue-list-head');head.append(node('strong','',`${overviewUI.section==='notes'?'Reference findings':overviewUI.showAll?'All findings':'Findings'} (${all.length})`));box.append(head);
  const filter=node('select','issue-filter');filter.setAttribute('aria-label','Which findings to show');
  for(const [value,label] of [['active','Needs attention'],['all','Include reviewed & reference items']]){const option=node('option','',label);option.value=value;filter.append(option);}
  filter.value=overviewUI.showAll?'all':'active';filter.addEventListener('change',()=>{overviewUI.showAll=filter.value==='all';renderOverviewIssues(true);});box.append(filter);
  filter.hidden=overviewUI.section==='notes';
  const search=node('input','issue-search');search.type='search';search.placeholder='Find a part or issue…';search.setAttribute('aria-label','Find a part or issue');search.value=overviewUI.search;
  search.addEventListener('input',()=>{const position=search.selectionStart;overviewUI.search=search.value;renderOverviewIssues(true);const next=box.querySelector('input');next.focus();try{next.setSelectionRange(position,position);}catch{}});box.append(search);
  const list=node('div','overview-issue-scroll');
  for(const {group,action} of items){
    const button=node('button',`overview-issue${overviewUI.focus===group.id?' active':''}`);button.type='button';button.setAttribute('aria-pressed',String(overviewUI.focus===group.id));
    const refs=group.components.length>4?`${group.components.length} components`:group.components.join(', ') || 'Whole board';
    const copy=node('span','issue-copy');copy.append(node('small','',`${group.id} · ${refs}`),node('strong','',issueTitle(group,action)),node('span','finding-state',findingLabel(group,action)));button.append(copy);
    button.addEventListener('click',()=>openOverviewIssue(group.id));list.append(button);
  }
  if(!items.length)list.append(node('p','field-help',overviewUI.search?'No matching issues. Try a part name such as U8.':'No items needing attention in this section.'));
  box.append(list);
  list.scrollTop=scrollTop;
  if(listOnly)return;
  const current=all.find(item=>item.group.id===overviewUI.focus);if(!current){renderSectionEmpty(detail);return;}
  detail.classList.remove('empty-review');
  const {group,action}=current;
  const header=node('div','issue-heading');header.append(node('span','kicker',`${group.id} · ${findingLabel(group,action)}`),node('h2','',issueTitle(group,action)));
  const next=node('button','text-button','Next finding →');next.type='button';next.hidden=all.length<2;next.addEventListener('click',()=>openOverviewIssue(all[(all.indexOf(current)+1)%all.length].group.id));const chatButton=node('button','secondary','Issue chat');chatButton.type='button';chatButton.addEventListener('click',()=>openIssueChat(group.id));header.append(chatButton,next);detail.append(header);
  const body=node('div','issue-layout'),inspector=node('div','issue-inspector'),reason=node('details','issue-analysis');
  const scopeNote=app.project.review_sections?.assignments?.[group.id]?.scope_note;
  if(scopeNote){
    inspector.append(node('p','review-notice',scopeNote));
    const scopeButton=node('button','secondary','Confirm product use');scopeButton.type='button';scopeButton.addEventListener('click',()=>{navigate('checks');renderCheckScope();});inspector.append(scopeButton);
  }
  const fixEntry=scopeNote?null:fileFixEntry(group,action);if(fixEntry)inspector.append(fixEntry);
  const designEntry=scopeNote?null:(typeof coordinatedFixEntry==='function'?coordinatedFixEntry(group,action):null);if(designEntry)inspector.append(designEntry);
  if(action?.action_kind==='order_setting')inspector.append(node('p','review-notice','Set this when ordering your PCB. No KiCad file edit is needed for this requirement.'));
  inspector.append(node('h3','','Observation'),node('p','observed-fact',explainIssueText(issueObservation(group,action))));
  if(group.outcome){
    inspector.append(node('p','field-help',`Repair outlook: ${outcomeLabel(group)} — ${group.outcome_why || 'See the analysis below.'}`));
  }
  const explain=node('button','text-button','Explain in simpler words');explain.type='button';explain.disabled=designUI.chatBusy || app.busy;
  explain.addEventListener('click',()=>openStepChat(group.id,0,'Explain this issue more simply. Keep it short: what does it mean for my product, and what should I do next?'));
  inspector.append(explain);
  if(action?.manufacturer_check && app.tab!=='manufacturing'){const manufacturer=node('button','text-link','See manufacturer settings →');manufacturer.type='button';manufacturer.addEventListener('click',()=>selectTab('manufacturing'));inspector.append(manufacturer);}
  const suppressed=suppressedQuestions(group,action);
  const analysisRaw=String(action?.reason || group.why || '');
  const analysisText=filterStillNeeded(analysisRaw,suppressed);
  reason.append(node('summary','','Analysis & pin checks'),node('p','',explainIssueText(analysisText)));
  if(suppressed.length && analysisText!==analysisRaw)reason.append(node('p','field-help','One detail is being checked internally — nothing you need to answer.'));
  if(action?.context_response)reason.append(node('p','review-notice',action.context_response));
  if(group.net_aliases?.length){
    reason.append(node('h4','','What the app checked'));
    for(const alias of group.net_aliases){
      const card=node('section','pin-analysis');
      card.append(node('strong','',`${alias.names.join(' / ')} → ${alias.chosen_net}`),node('p','',alias.explanation));
      for(const pin of alias.pins){
        card.append(node('p','',`${pin.reference} pin ${pin.pin} (${pin.name}): schematic ${pin.schematic_net || 'unknown'}; PCB ${pin.pcb_nets.join(', ') || 'not found'}. ${pin.board_matches?'The saved connections match.':'The match could not be confirmed.'}`));
      }
      if(alias.intent_question)card.append(node('p','field-help',alias.intent_question+' If you meant one connection, you can leave both names or remove the extra label for clarity.'));
      reason.append(card);
    }
  }
  if(action?.pin_checks?.length){
    reason.append(node('h4','','What the app checked'));
    for(const check of action.pin_checks){
      const card=node('section','pin-analysis');
      const decision={keep:'No wiring change needed',change:'Change this connection',needs_context:'One detail needed'}[check.decision];
      card.append(node('strong','',`${check.reference} · pin ${check.pin} → ${check.net || 'no net'} · ${decision}`),node('p','',explainIssueText(check.explanation)));
      let hidden=0;
      for(const question of check.missing_context || []){
        if(!questionPosed(check,question)){hidden++;continue;}
        card.append(node('p','review-notice',reviewInstruction(question)));
      }
      if(hidden)card.append(node('p','field-help','One detail is being checked internally — nothing you need to answer.'));
      reason.append(card);
    }
  }
  const evidence=node('section','issue-evidence');evidence.id='selected-proof';
  const viewer=node('section','issue-viewer');viewer.id='issue-viewer';
  const proofDetails=node('div');proofDetails.id='proof-details';proofDetails.append(node('p','field-help','Loading the original findings and any cited datasheet image…'));
  const proofDisclosure=node('details','proof-disclosure');proofDisclosure.append(node('summary','','Source evidence'),proofDetails);evidence.append(viewer,proofDisclosure);
  proofDisclosure.addEventListener('toggle',()=>{if(proofDisclosure.open)loadIssueProof(group);});
  const orderOnly=action?.action_kind==='order_setting';
  const fix=node('section','issue-fix');fix.append(node('h3','',orderOnly?'Order settings':'Action'));
  if(action?.analysis_limit)fix.append(node('p','review-notice',action.analysis_limit));
  if(action?.reference_only)fix.append(node('p','review-notice','No pad edit is proposed. This default-rule warning alone does not establish a footprint problem.'));
  if(action?.pin_checks?.length && action.pin_checks.every(p=>p.decision==='keep') && action.disposition!=='no_change')fix.append(node('p','review-notice','No wire edit is proposed for these pins. The comparison is conditional on the supplied part identity and operating assumptions.'));
  if(action?.disposition==='no_change' && !action?.reference_only)fix.append(node('p','review-notice','No wiring change is indicated for this finding. There is no required fix step.'));
  const needsPinAnalysis=pinAnalysisPending(group,action);
  if(needsPinAnalysis){
    fix.append(node('p','field-help','Preflight has not established whether these connections need changing. This is unfinished analysis, not a task for you to check the datasheet.'));
    const analyze=node('button','primary',app.busy?'Preflight is analyzing…':app.config.key_configured?'Run connection analysis':'Connect Vector for analysis');analyze.type='button';analyze.dataset.designAction='analyze-pins';analyze.disabled=app.busy;
    analyze.addEventListener('click',()=>app.config.key_configured?retryAIReview(true):openSettings(()=>retryAIReview(true)));fix.append(analyze);
  }
  if((app.project.ai_review?.rejected_items || []).some(item=>item.group_id===group.id)){
    fix.append(node('p','field-help','Local guidance · Vector evidence checks did not pass.'));
  }
  const rawPlan=needsPinAnalysis?[]:issueSteps(action?.steps ?? [group.next_step]);
  // Steps built verbatim from a suppressed follow-up question are hidden
  // by the same creator selection (not reworded). Instructions stay.
  let hiddenSteps=0;
  const plan=rawPlan.filter(step=>{if(!stepPosed(action,step)){hiddenSteps++;return false;}return true;});
  if(!needsPinAnalysis && !plan.length && !action?.analysis_limit && !action?.reference_only && action?.disposition!=='no_change' && !action?.pin_checks?.length)
    fix.append(node('p','field-help','No file change is proposed for this note. Use Issue chat to discuss it.'));
  const steps=node('ol','change-steps'),remaining=node('ol','change-steps');remaining.start=2;
  for(const [index,step] of plan.entries()){
    const item=node('li');item.append(node('span','',reviewInstruction(step)));const help=node('button','step-ask','Explain');help.type='button';help.setAttribute('aria-label',`Explain step ${index+1}`);help.addEventListener('click',()=>openStepChat(group.id,index));item.append(help);(index?remaining:steps).append(item);
  }if(plan.length)fix.append(steps);
  if(plan.length>1){if(orderOnly)fix.append(remaining);else{const more=node('details','remaining-steps');more.append(node('summary','',`Remaining steps (${plan.length-1})`),remaining);fix.append(more);}}
  if(hiddenSteps)fix.append(node('p','field-help','One detail is being checked internally — nothing you need to answer.'));
  inspector.append(fix,consequenceCard(action?.consequence));
  const help=node('section','issue-help');help.id='issue-help';help.setAttribute('aria-label','Ask about this finding');if(chat)help.append(chat);inspector.append(help,reason);
  if(action){const intent=node('details','issue-intent');intent.append(node('summary','','Design intent / accepted condition'),intentEditor(action));inspector.append(intent);}
  body.append(evidence,inspector);detail.append(body);
  renderIssueViewer(group);
  if(app.tab!=='chat' && designUI.chatGroup!==group.id){designUI.chatGroup=group.id;designUI.chatStep=0;designUI.chatMessages=[];}renderStepChat();
  setBusy();
}

function consequenceCard(consequence) {
  const card=node('section','issue-consequence');
  card.append(node('h4','','Consequence'),
    node('p','',explainIssueText(consequence?.text || 'This check has not established what would happen if you leave it unchanged.')));
  if(consequence?.context_note)card.append(node('p','field-help',consequence.context_note));
  return card;
}

function reviewFailureNotice(ai, manufacturing=false) {
  const unsent=ai.status==='failed' && ai.request_usage?.requests===0;
  const removedLimit=String(ai.message || '').includes('300 kB AI evidence limit');
  return {
    title:manufacturing?'Manufacturer guidance uses local checks':ai.status==='stale'?'Vector review is out of date':unsent?'Vector review was not sent':'Vector review did not finish',
    message:(manufacturing?'These ordering instructions use saved board measurements and published manufacturer limits. They do not depend on AI. ':'')+
      (ai.status==='stale'?'The design context changed. Update the review to get Vector advice for the current context.':
        removedLimit?'This saved run stopped at the former 300 kB evidence limit. That limit has been removed. Select Retry Vector review to review this saved revision.':
        ai.message || 'The saved review has no failure details. Open the review log for its recorded status.'),
    unsent
  };
}

function renderOverview() {
  if(!app.project)return;
  const items=overviewActions();if(!items.some(item=>item.group.id===overviewUI.focus))overviewUI.focus=items[0]?.group.id || null;
  const chat=$('step-chat');if(chat)$('panel-actions').append(chat);
  const main=$('overview-main');main.replaceChildren();
  const host=overviewUI.section==='manufacturing'?$('manufacturer-review'):$('panel-overview');if(host && main.parentElement!==host)host.append(main);
  const coverage=app.project.review_sections?.coverage || {},ai=app.project.ai_review || {},rejected=ai.rejected_items || [];
  const status=node('div','review-status');
  status.hidden=overviewUI.section==='manufacturing';
  status.append(node('span','check-status',coverage.drc_ran && coverage.erc_ran?'KiCad checks completed':'Local check coverage is limited'));
  const audit=ai.circuit_audit;
  const aiLabel=ai.status==='needs_context'?'Answer product questions':ai.status==='completed'?(audit?`Circuit analysis · ${audit.reviewed_count}/${audit.target_count} parts`:rejected.length?`Automated review · ${rejected.length} unresolved`:'Automated review complete'):ai.status==='failed'?'Automated review failed':ai.status==='stale'?'Automated review out of date':'Automated review not run';
  const aiButton=node('button',`status-detail${rejected.length || ai.status==='failed'?' needs-review':''}`,aiLabel);aiButton.type='button';aiButton.title='Vector interpretation and validation details';aiButton.addEventListener('click',()=>selectTab('ai'));status.append(aiButton);
  const edit=node('button','text-button','Design intent');edit.type='button';edit.addEventListener('click',openSharedContext);status.append(edit);main.append(status);
  if(['failed','stale'].includes(ai.status)){
    const notice=node('div','review-notice');notice.setAttribute('role','status');
    const detail=reviewFailureNotice(ai,overviewUI.section==='manufacturing');
    notice.append(node('strong','',detail.title),node('p','',detail.message));
    if(detail.unsent)notice.append(node('p','field-help','0 Vector requests sent for this review.'));
    notice.append(aiRetryButton());
    const log=node('button','text-button','View review log');log.type='button';log.addEventListener('click',()=>selectTab('ai'));notice.append(log);
    main.append(notice);
  }
  const sourceStatus=app.project.workspace?.source_status;
  if(sourceStatus?.status==='changed'){
    const stale=node('div','review-notice');stale.setAttribute('role','status');
    stale.append(node('strong','','This review uses an older copy of your KiCad files.'),
      node('p','',`Changed: ${(sourceStatus.changed || []).join(', ') || 'some linked files are missing'}. Save in KiCad, then select Update review before using these tips.`));
    main.append(stale);
  }
  const ask=node('button','text-button review-ask',overviewUI.focus?'Issue chat':'Vector chat');ask.type='button';ask.addEventListener('click',()=>{
    if(overviewUI.focus && $('step-chat-question')){if($('issue-help'))$('issue-help').open=true;$('step-chat').scrollIntoView({behavior:'smooth',block:'center'});$('step-chat-question').focus();}
    else openChatHome();
  });status.append(ask);
  renderReviewSections(main);
  const tools=node('div','review-tools');
  if(['connections','board'].includes(overviewUI.section))renderRoutingExplorer(tools);
  if(overviewUI.section==='connections')renderCircuitAudit(tools);
  if(overviewUI.section==='board')renderTraceMeasurements(tools);
  if(tools.childNodes.length){
    const toolbar=main.querySelector('.review-toolbar');toolbar.append(tools);
    for(const disclosure of tools.querySelectorAll('details'))disclosure.addEventListener('toggle',()=>toolbar.classList.toggle('tools-open',!!tools.querySelector('details[open]')));
  }
  const layout=node('div','review-workspace'),issues=node('aside','issue-list'),detail=node('article','issue-detail');
  issues.id='overview-issues';detail.id='overview-detail';layout.append(issues,detail);main.append(layout);renderOverviewIssues();
  if(typeof syncReviewNavigation==='function')syncReviewNavigation();
}

function renderCircuitAudit(parent){
  const audit=app.project?.ai_review?.circuit_audit;
  if(!audit)return;
  const panel=node('details','circuit-audit');
  panel.append(node('summary','',`Circuit analysis · ${audit.reviewed_count}/${audit.target_count} parts`));
  panel.append(node('p','field-help',`${audit.check_count} comparisons · ${audit.concern_count} concerns · ${audit.unresolved_count} need more information. These are automated interpretations of saved connections and selected datasheet requirements.`));
  const labels={power:'Power & return',interface:'Interfaces',configuration:'Startup & configuration',external_parts:'Supporting components',signal_path:'Signal path'};
  const statuses={consistent:'Consistent with requirement',concern:'Correction to review',unresolved:'Missing information',not_applicable:'Not applicable'};
  for(const part of audit.parts || []){
    const row=node('details','circuit-audit-part'),heading=node('summary');
    const counts=part.checks.filter(c=>c.status==='concern').length,gaps=part.checks.filter(c=>c.status==='unresolved').length;
    heading.append(node('strong','',part.reference),node('span','',part.role),node('small','',counts?`${counts} concerns`:gaps?`${gaps} open questions`:'Comparisons recorded'));row.append(heading);
    if(part.part_match==='provisional')row.append(node('p','field-help','Comparison assumes the automatically matched part variant.'));
    for(const check of part.checks){
      const section=node('section',`circuit-audit-check ${check.status}`),head=node('div','audit-check-heading');
      head.append(node('h4','',labels[check.area] || check.area),node('span','',statuses[check.status] || check.status));section.append(head,node('p','',check.explanation));
      if(check.comparison){const comparison=node('details','audit-evidence');comparison.append(node('summary','','Engineering comparison'));for(const [key,label] of [['requirement','Requirement'],['observed_design','Your design'],['reasoning','Why'],['consequence','Consequence']]){comparison.append(node('strong','',label),node('p','',check.comparison[key]));}section.append(comparison);}
      if(check.missing_context?.length){
        let hidden=0;
        for(const question of check.missing_context){
          if(!questionPosed(check,question)){hidden++;continue;}
          section.append(node('p','audit-question',reviewInstruction(question)));
        }
        if(hidden)section.append(node('p','field-help','One detail is being checked internally — nothing you need to answer.'));
      }
      if(check.observations?.length || check.citations?.length){
        const evidence=node('details','audit-evidence');evidence.append(node('summary','','Pins & requirements'));
        for(const pin of check.observations || [])evidence.append(node('p','audit-pin',`${pin.reference} pin ${pin.pin} → ${pin.net || 'No net'}`));
        for(const citation of check.citations || []){
          const quote=node('blockquote','',citation.quote),source=(app.project.datasheets?.excerpts || audit.documents || []).find(d=>d.evidence_id===citation.evidence_id);
          if(source)sourceLinks(quote,[{url:source.url,title:`${source.mpn || part.reference} · PDF page ${source.page}`}]);
          evidence.append(quote);
        }
        section.append(evidence);
      }
      row.append(section);
    }
    panel.append(row);
  }
  for(const item of audit.unreviewed || []){
    const row=node('p','audit-gap');row.append(node('strong','',`${item.reference} · Not reviewed. `),document.createTextNode(item.message));panel.append(row);
  }
  parent.append(panel);
}

function openSharedContext(){selectTab('project');$('design-panel').querySelector('.context-editor').open=true;$('device-context').focus();}

function chooseReviewSection(section){
  section=reviewSection(section);
  if(app.tab==='manufacturing' && section!=='manufacturing')return selectTab(section);
  overviewUI.section=section;overviewUI.focus=null;overviewUI.search='';overviewUI.showAll=false;
  overviewUI.chainView=false;overviewUI.chainPin=null;overviewUI.sheetFocus=null;overviewUI.target=0;
  overviewUI.view=['connections','notes'].includes(section)?'schematic':'pcb';renderOverview();
  if(typeof syncReviewNavigation==='function')syncReviewNavigation();
}

function renderReviewSections(parent){
  const review=app.project.review_sections || {},coverage=review.coverage || {};
  const total=Object.values(review.assignments || {}).filter(item=>item.attention).length;
  if($('tab-release'))$('tab-release').textContent=`Overview · ${total} findings`;
  for(const section of review.sections || []){
    const count=$('count-'+section.id);if(count){count.textContent=section.attention_count;count.title=`${section.attention_count} findings`;}
  }
  if($('count-board'))$('count-board').textContent=Object.values(review.assignments || {}).filter(item=>item.attention && reviewSection(item.section)==='board').length;
  const toolbar=node('div','review-toolbar'),title=node('div');
  const titles={connections:'Circuit connections',board:'PCB & signals',manufacturing:'Manufacturing requirements',notes:'KiCad & library notes'};
  title.append(node('h2','',titles[overviewUI.section]));
  const pending=overviewUI.section==='connections' && pendingPinGroupCount();
  if(pending)title.append(node('span','pending-count',`Preflight has not finished ${pending} pin ${pending===1?'analysis':'analyses'}`));
  toolbar.append(title);
  const tools=node('div','review-toolbar-actions'),notes=node('button','text-button',`KiCad notes (${review.notes_count || 0})`);notes.type='button';notes.addEventListener('click',()=>chooseReviewSection('notes'));tools.append(notes);
  const scope=node('details','section-coverage');scope.append(node('summary','','Check scope'));
  const text={connections:`Documented pin requirements, saved connections and circuit paths. ${coverage.pin_comparison?`${coverage.pins_compared} pin assignments compared.`:'Pin comparison unavailable.'} ERC warnings need interpretation. No listed correction does not prove the circuit works.`,
    board:`Placement, routing and signal paths. ${coverage.drc_ran?'KiCad checked board rules, shorts and unconnected items.':'Physical continuity has not been checked.'} ${coverage.traced_paths || 0} paths traced; ${coverage.dc_estimates || 0} simple trace-only DC estimates. RF impedance, return planes, noise and heating are not simulated.`,
    manufacturing:'Measured board geometry compared with the selected process. Order options and production-file acceptance still need confirmation.',
    notes:'Original ERC, library and coverage findings. These are not automatically functional wiring errors.'};
  scope.append(node('p','',text[overviewUI.section]));tools.append(scope);toolbar.append(tools);parent.append(toolbar);
}

function renderSectionEmpty(detail){
  const review=app.project.review_sections || {},coverage=review.coverage || {};
  const connection=overviewUI.section==='connections';
  const state=reviewCompletionState();
  detail.classList.add('empty-review');
  if(state.kind==='running'){
    detail.append(node('h2','','Review in progress'));
    detail.append(node('p','','PreFlight is still analyzing this version. Findings will appear here when the checks finish.'));
    return;
  }
  if(state.kind==='clean'){
    const panel=node('div','review-success');
    panel.append(node('h2','','No PCB errors detected!'));
    panel.append(node('p','','PreFlight found no issues in the completed checks. Have questions or want to double-check something? Ask Vector Chat.'));
    const ask=node('button','primary','Ask Vector');ask.type='button';
    ask.addEventListener('click',()=>openChatHome());
    panel.append(ask);detail.append(panel);
  }else if(state.kind==='gaps'){
    detail.append(node('h2','','No findings, but some checks are incomplete'));
    detail.append(node('p','','PreFlight found no issues so far, but this board cannot be confirmed error-free until the checks below finish.'));
    for(const gap of state.gaps)detail.append(node('p','review-notice',gap));
  }else{
    const pending=connection && pendingPinGroupCount();
    detail.append(node('h2','',pending?'Pin review incomplete':connection?'No wiring correction identified':'No active findings'));
    detail.append(node('p','',pending?`${pending} pin groups still need a supported interpretation. No wire change is proposed.`:connection?'No specific wire change is supported by this run. This is not a circuit sign-off.':'See reference items or check scope for the remaining limits.'));
    if(connection && coverage.ai_status!=='completed')detail.append(node('p','review-notice','The Vector circuit review has not completed for this version.'));
  }
  const compared=connection?comparedPins():[];
  if(compared.length){
    const results=node('details','issue-analysis');results.append(node('summary','',`${compared.length} flagged pin connections compared`));
    results.append(node('p','field-help','These comparisons use the saved connections and matching datasheet passages. Part identity and operating assumptions still apply.'));
    for(const check of compared){
      const row=node('section','pin-analysis');row.append(node('strong','',`${check.reference} pin ${check.pin} → ${check.net}`),node('p','',check.explanation));results.append(row);
    }
    detail.append(results);
  }
  if(connection && review.pending_erc_ids?.length){
    const pending=node('button','secondary','View pending checks');pending.type='button';pending.addEventListener('click',()=>chooseReviewSection('notes'));detail.append(pending);
  }
  const edit=node('button','text-button','Add device context');edit.type='button';edit.addEventListener('click',openSharedContext);detail.append(edit);
  if(['connections','board'].includes(overviewUI.section)){
    const viewer=node('section','issue-viewer');viewer.id='issue-viewer';detail.append(viewer);
    // Circuit paths remain browsable even when the section has no findings.
    renderIssueViewer(currentViewerGroup());
  }
}

function currentViewerGroup(){return (app.project.review?.groups || []).find(g=>g.id===overviewUI.focus) || {id:'path-view',components:[],issue_ids:[]};}

function renderTraceMeasurements(parent){
  const data=app.project.signal_paths?.board_measurements,box=node('details','trace-measurements');box.append(node('summary','','Trace measurements'));
  if(!data){box.append(node('p','field-help','Click Update to measure traces and read the stackup from your current PCB.'));parent.append(box);return;}
  const copper=(data.stackup?.layers || []).filter(l=>l.type==='copper');
  box.append(node('p','',copper.length?`Saved copper: ${copper.map(l=>`${l.name} ${l.thickness_mm ?? '?'} mm`).join(' · ')}`:'No copper thickness is saved. Resistance cannot be estimated.'),node('p','field-help','DC estimates apply only to one unbranched track between its drawn endpoints at 25 °C. They exclude pads, parts and heating. A dash means uncalculated, not zero.'));
  const scroll=node('div','measurements-scroll'),table=node('table'),thead=node('thead'),head=node('tr');
  for(const label of ['Net','Drawn length','Min. width','Vias','Trace-only DC estimate'])head.append(node('th','',label));thead.append(head);table.append(thead);
  const tbody=node('tbody');
  for(const n of data.nets || []){
    const row=node('tr'),dc=n.dc_trace || {};row.append(node('td','',n.net),node('td','',`${n.drawn_mm} mm`),node('td','',n.min_width_mm==null?'—':`${n.min_width_mm} mm`),node('td','',String(n.vias)));
    const cell=node('td');if(dc.status==='estimated')cell.append(node('strong','',`${(dc.ohms*1000).toFixed(2)} mΩ`),node('small','',`${dc.layer} · endpoints ${dc.endpoints_mm.map(p=>p.join(', ')).join(' → ')} mm`));
    else cell.append(node('span','','—'),node('small','',dc.reason || 'Unavailable'));row.append(cell);tbody.append(row);
  }
  table.append(tbody);scroll.append(table);box.append(scroll);list(box,data.limits || []);
  const method=node('details');method.append(node('summary','','Calculation method'),node('p','',data.resistance_model));sourceLinks(method,[{url:data.resistance_source,title:'Texas Instruments · trace resistance formula'}]);box.append(method);parent.append(box);
}

function renderIssueViewer(group) {
  const box=$('issue-viewer');if(!box)return;box.replaceChildren();
  const toolbar=node('div','viewer-toolbar');for(const [value,label] of [['schematic','Schematic'],['pcb','PCB']]){
    const b=node('button',overviewUI.view===value?'selected':'',label);b.type='button';b.setAttribute('aria-pressed',String(overviewUI.view===value));b.addEventListener('click',()=>{overviewUI.view=value;renderIssueViewer(group);});toolbar.append(b);
  }
  const filename=app.project.selected_files?.[overviewUI.view==='pcb'?'pcb':'schematic'];
  if(filename){const source=node('span','viewer-source',filename.split('/').pop());source.title='Checked source: '+filename;toolbar.append(source);}
  box.append(toolbar);
  if(overviewUI.chainView){
    const chain=selectedRoutingChain();box.append(node('p','review-notice','Viewing circuit path: '+chain.title));
    const reset=node('button','text-button','Back to the issue location');reset.type='button';reset.addEventListener('click',()=>{overviewUI.chainView=false;overviewUI.chainPin=null;overviewUI.target=0;overviewUI.sheetFocus=null;renderIssueViewer(group);});box.append(reset);
  }
  if(overviewUI.view==='pcb'){
    const pcb=node('div');pcb.id='overview-map';box.append(pcb);renderOverviewMap();return;
  }
  const visual=app.project.workspace?.schematic_visual;
  if(visual?.status!=='ready'){
    box.append(node('p','field-help',overviewUI.schematicLoading?'Rendering your schematic with KiCad…':visual?.message || 'The schematic preview is not available for this review. Other checks are unaffected.'));
    if(!overviewUI.schematicLoading && overviewUI.schematicTried!==app.snapshot.report_id){
      overviewUI.schematicLoading=true;overviewUI.schematicTried=app.snapshot.report_id;
      const reportId=app.snapshot.report_id;
      api('/api/schematic-preview',{report_id:reportId}).then(meta=>{if(app.snapshot.report_id===reportId)app.project.workspace.schematic_visual=meta;}).catch(error=>{if(app.snapshot.report_id===reportId)showError(error);}).finally(()=>{overviewUI.schematicLoading=false;if(app.snapshot.report_id===reportId)renderIssueViewer(currentViewerGroup());});
    }
    return;
  }
  let displayGroup=routingViewerGroup(group);
  if(!overviewUI.chainView){
    const sites=boardSites(displayGroup,app.project),site=sites[overviewUI.boardSiteScope===app.snapshot.report_id+':'+group.id?Math.min(overviewUI.boardSite || 0,sites.length-1):0];
    if(site && (group.signal_path_id || (group.issue_ids || []).some(fid=>app.project.issues?.[Number(fid.slice(1))-1]?.source==='kicad_drc'))){
      const pins=site.items.filter(p=>p.reference && p.pin);
      if(pins.length)displayGroup={...displayGroup,components:[...new Set(pins.map(p=>p.reference))],chain_targets:pins,issue_ids:[]};
    }
  }
  const targets=schematicTargets(displayGroup,visual,app.project);
  if(overviewUI.preferredPin){const index=targets.findIndex(p=>p.reference+':'+p.pin===overviewUI.preferredPin);if(index>=0)overviewUI.target=index;}
  if(overviewUI.chainView && overviewUI.chainPin){const index=targets.findIndex(p=>p.reference+':'+p.pin===overviewUI.chainPin);if(index>=0)overviewUI.target=index;}
  if(overviewUI.target>=targets.length)overviewUI.target=0;
  const selected=targets[overviewUI.target];
  if(targets.length){
    const row=node('div','location-picker');const label=node('label','','Show this location');label.htmlFor='schematic-location';
    const picker=node('select');picker.id='schematic-location';picker.setAttribute('aria-label','Schematic evidence location');
    for(const [index,target] of targets.entries()){const option=node('option','',target.label);option.value=String(index);picker.append(option);}
    picker.value=String(overviewUI.target);picker.addEventListener('change',()=>{overviewUI.target=Number(picker.value);const p=targets[overviewUI.target];overviewUI.preferredPin=p.pin?p.reference+':'+p.pin:null;if(overviewUI.chainView)overviewUI.chainPin=overviewUI.preferredPin;overviewUI.sheetFocus=null;renderIssueViewer(group);});row.append(label,picker);box.append(row);
  }
  const focus=()=>{
    if(!selected)return visual.bounds.slice();
    if(overviewUI.chainView && !overviewUI.chainPin){const xs=targets.map(p=>p.point[0]),ys=targets.map(p=>p.point[1]);return [Math.min(...xs)-8,Math.min(...ys)-8,Math.max(40,Math.max(...xs)-Math.min(...xs)+16),Math.max(30,Math.max(...ys)-Math.min(...ys)+16)];}
    if(selected.kind==='pin')return [selected.point[0]-25,selected.point[1]-20,50,40];
    const points=Object.values(visual.pins?.[selected.ref] || {}).map(pin=>pin.point);
    points.push(selected.point);
    const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
    return [Math.min(...xs)-10,Math.min(...ys)-10,Math.max(40,Math.max(...xs)-Math.min(...xs)+20),Math.max(30,Math.max(...ys)-Math.min(...ys)+20)];
  };
  const scope=app.snapshot.report_id+':'+group.id+':'+overviewUI.target;
  if(overviewUI.sheetFocus!==scope){overviewUI.sheetBox=focus();overviewUI.sheetFocus=scope;}
  const controls=node('div','schematic-controls');
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('schematic-canvas');svg.setAttribute('viewBox',overviewUI.sheetBox.join(' '));svg.setAttribute('aria-label',group.id==='path-view' && !overviewUI.chainView?'Saved schematic':`Schematic around ${displayGroup.components.join(', ') || 'this issue'}`);svg.setAttribute('role','img');
  const image=document.createElementNS(svg.namespaceURI,'image');image.setAttribute('href',`/reports/${encodeURIComponent(app.snapshot.report_id)}/schematic.svg`);
  for(const [key,value] of Object.entries({x:visual.bounds[0],y:visual.bounds[1],width:visual.bounds[2],height:visual.bounds[3]}))image.setAttribute(key,value);svg.append(image);
  for(const target of overviewUI.chainView && overviewUI.chainPin && selected?[selected]:targets){
    const circle=document.createElementNS(svg.namespaceURI,'circle');circle.setAttribute('cx',target.point[0]);circle.setAttribute('cy',target.point[1]);circle.setAttribute('r',target.kind==='pin'?'1.4':'5');circle.setAttribute('class','schematic-focus'+(target!==selected?' secondary-target':'')+(target.approximate?' context-target':''));circle.setAttribute('vector-effect','non-scaling-stroke');circle.dataset.label=target.label;const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=target.label;circle.append(title);svg.append(circle);
    if(target===selected && !target.approximate){const [x,y]=target.point;svg.append(svgNode('path',{d:`M ${x-2.6} ${y} h 1 M ${x+1.6} ${y} h 1 M ${x} ${y-2.6} v 1 M ${x} ${y+1.6} v 1`,class:'schematic-focus','vector-effect':'non-scaling-stroke'}));}
  }
  const update=()=>svg.setAttribute('viewBox',overviewUI.sheetBox.join(' '));
  for(const [label,fn] of [['−',()=>zoom(1.35)],['+',()=>zoom(.74)],['Whole sheet',()=>{overviewUI.sheetBox=visual.bounds.slice();update();}],[overviewUI.chainView?'Focus path':'Focus issue',()=>{overviewUI.sheetBox=focus();update();}]]){
    if(label==='Focus issue' && !selected)continue;
    const b=node('button','text-button',label);b.type='button';b.setAttribute('aria-label',label==='−'?'Zoom out':label==='+'?'Zoom in':label);b.addEventListener('click',fn);controls.append(b);
  }
  function zoom(factor){let [x,y,w,h]=overviewUI.sheetBox;if(w*factor<4 || w*factor>3000)return;overviewUI.sheetBox=[x+w*(1-factor)/2,y+h*(1-factor)/2,w*factor,h*factor];update();}
  let drag=null;svg.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,b:overviewUI.sheetBox.slice()};svg.setPointerCapture(e.pointerId);});
  svg.addEventListener('pointermove',e=>{if(!drag)return;const scale=Math.min(svg.clientWidth/drag.b[2],svg.clientHeight/drag.b[3]);overviewUI.sheetBox=[drag.b[0]-(e.clientX-drag.x)/scale,drag.b[1]-(e.clientY-drag.y)/scale,drag.b[2],drag.b[3]];update();});
  svg.addEventListener('pointerup',()=>drag=null);svg.addEventListener('pointercancel',()=>drag=null);
  box.append(controls,svg,node('p','field-help',selected?`${visual.root_only?'Root sheet only. ':''}${selected.approximate?'Component context only: ':'Crosshair: '}${selected.label}${selected.hidden?' (hidden pin; not drawn by the symbol)':''}. ${selected.approximate?'An exact pin has not been identified. ':''}Drag to move; use + / − to zoom.`:group.id==='path-view'?'Saved schematic · Drag to move; use + / − to zoom.':'No exact location is available on this sheet. This image shows the whole sheet; it does not mark an unverified position.'));
}

function schematicTargets(group,visual,project){
  const targets=[],seen=new Set();
  const add=(entry)=>{if(!entry?.point || entry.point.some(v=>!Number.isFinite(v)))return;const key=entry.kind==='pin'?entry.reference+':'+(entry.pin || entry.point.join(',')):entry.point.join(',')+':'+entry.label;if(seen.has(key))return;seen.add(key);targets.push({...entry,ref:entry.reference || entry.ref || ''});};
  const action=(project.action_plan?.actions || []).find(a=>a.group_id===group.id);
  const correctiveSteps=[...(action?.steps || []),group.next_step || ''].join(' ');
  let issuePin=null;
  for(const reference of group.components || []){
    const escaped=reference.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    const patterns=[
      new RegExp(`\\bpin\\s+([A-Za-z0-9]+)(?:\\s*\\([^)]*\\))?\\s+(?:of|on)\\s+${escaped}\\b`,'i'),
      new RegExp(`\\b${escaped}\\s+pin\\s+([A-Za-z0-9]+)\\b`,'i')
    ];
    for(const pattern of patterns){const match=correctiveSteps.match(pattern);if(match){issuePin={reference,pin:match[1]};break;}}
    if(issuePin)break;
  }
  const chainTargets=group.chain_targets || [];
  const primary=issuePin && chainTargets.find(pin=>pin.reference===issuePin.reference && String(pin.pin)===String(issuePin.pin));
  // When the review step names the suspect pin, don't make a comparison pin
  // look like the fault. If that pin isn't renderable, fall back to component
  // context below instead of marking a different pin as the issue.
  const visibleChainTargets=issuePin?(primary?[primary]:[]):chainTargets;
  for(const pin of visibleChainTargets){const entry=visual.pins?.[pin.reference]?.[pin.pin];if(entry)add({...entry,label:entry.label+(entry.name?' · '+entry.name:'')+(primary?' · review this pin':''),issueTarget:Boolean(primary)});}
  const checks=action?.pin_checks || [];
  const actionable=checks.some(p=>p.decision==='change' || p.decision==='needs_context');
  for(const pin of checks.filter(p=>!actionable || p.decision!=='keep')){const entry=visual.pins?.[pin.reference]?.[pin.pin];if(entry)add({...entry,label:entry.label+(entry.name?' · '+entry.name:'')+(pin.decision==='change'?' · change this connection':''),decision:pin.decision});}
  // Highlight the verified neighbours named in the component descriptions,
  // using the same references as the finding text. Ground nets are skipped:
  // nearly the whole board would light up and hide the relevant connection.
  if(typeof componentDescriptions==='function'){
    let extra=0;
    for(const ref of group.components || []){
      for(const endpoint of componentDescriptions()[ref]?.endpoints || []){
        if(endpoint.ground || extra>=6)continue;
        for(const [otherRef,otherPin] of endpoint.others || []){
          if(extra>=6)break;
          const entry=visual.pins?.[otherRef]?.[otherPin];if(!entry)continue;
          add({...entry,label:`${entry.label}${entry.name?' · '+entry.name:''} · connected to ${ref}`,context:true});extra++;
        }
      }
    }
  }
  for(const fid of group.issue_ids || []){
    const index=/^F\d+$/.test(fid)?Number(fid.slice(1))-1:-1,issue=project.issues?.[index];
    if(issue?.source!=='kicad_erc')continue;
    for(const item of issue.items || []){
      const entry=visual.item_locations?.[item.uuid];
      if(entry)add({...entry,label:entry.label+(entry.name?' · '+entry.name:'')});
    }
  }
  if(!targets.length)for(const ref of group.components || [])for(const point of visual.locations?.[ref] || [])add({ref,reference:ref,label:ref,point,kind:'symbol',approximate:true});
  return targets.sort((a,b)=>Number(Boolean(b.issueTarget))-Number(Boolean(a.issueTarget)) || Number(b.decision==='change')-Number(a.decision==='change') || Number(a.ref.startsWith('#'))-Number(b.ref.startsWith('#')));
}

function loadIssueProof(group) {
  const key=app.snapshot.report_id+':'+group.id;
  if(overviewUI.proof.has(key)){renderIssueProof(group,overviewUI.proof.get(key));return;}
  if(overviewUI.loading.has(key))return;overviewUI.loading.add(key);
  api('/api/issue-proof',{report_id:app.snapshot.report_id,group_id:group.id}).then(result=>{
    overviewUI.proof.set(key,result);if(app.snapshot.report_id+':'+overviewUI.focus===key)renderIssueProof(group,result);
  }).catch(error=>{if(app.snapshot.report_id+':'+overviewUI.focus===key){const box=$('proof-details'),retry=node('button','secondary','Reload evidence');retry.type='button';retry.addEventListener('click',()=>loadIssueProof(group));box.replaceChildren(node('p','',error.message),retry);}}).finally(()=>overviewUI.loading.delete(key));
}

function renderIssueProof(group,proof) {
  const box=$('proof-details');if(!box)return;box.replaceChildren();
  if(!group.net_aliases?.length)for(const check of proof.identity_checks || [])box.append(node('p','review-notice',check));
  const fact=node('div','proof-file');fact.append(node('h4','','What your files show'),node('p','',proof.observed));
  const raw=node('details');raw.append(node('summary','','Exact findings & file locations'));list(raw,proof.locations);
  for(const issue of proof.rule_findings || []){raw.append(node('p','',issue.message));list(raw,issue.locations || []);}fact.append(raw);box.append(fact);
  if(proof.pictures?.length){
    for(const picture of proof.pictures){const figure=node('figure','proof-figure');figure.append(node('h4','',`From the datasheet · page ${picture.page}`));
      figure.append(node('p','field-help',picture.title));
      const link=node('a');link.href=picture.image;link.target='_blank';link.rel='noopener';link.setAttribute('aria-label','Open datasheet evidence image');
      const img=node('img');img.src=picture.image;img.alt=picture.matched?'Actual datasheet passage with cited text highlighted':`Original datasheet page ${picture.page}`;img.addEventListener('error',()=>{link.replaceChildren(node('p','field-help','The evidence image could not load. Use the original datasheet link below.'));});link.append(img);figure.append(link,node('figcaption','',picture.caption));
      if(picture.quote)figure.append(node('blockquote','datasheet-quote',picture.quote));
      sourceLinks(figure,[{url:picture.url,title:'Open original datasheet'}]);box.append(figure);
    }
  }else{const reason=group.net_aliases?.length?'The evidence here is KiCad’s exported connections and the matching PCB pad assignments. A datasheet is not needed to explain how KiCad chose the net name.':group.sources?.length?'A source is linked below, but an exact supporting screenshot is not available. Treat this as a review item.':'This finding comes from the saved project or KiCad rules. There is no datasheet claim attached to it.';box.append(node('p','proof-limit',reason));sourceLinks(box,proof.sources);}
  for(const message of proof.notices || [])box.append(node('p','field-help',message));
  box.append(node('p','proof-limit',group.net_aliases?.length?'This naming notice does not establish a wiring fault. It also does not check whether the supply voltage meets the chip’s requirements.':proof.limit));
}

function routingChains(){return app.project?.circuit?.routing_chains?.chains || [];}
function selectedRoutingChain(){return routingChains().find(c=>c.id===overviewUI.chainId);}
function viewedRoutingChain(){
  const chain=selectedRoutingChain();if(!chain?.routes?.length)return chain;
  const longest=chain.routes.reduce((best,r,index)=>r.steps.length>chain.routes[best].steps.length?index:best,0);
  const index=Number.isInteger(overviewUI.chainRoute) && overviewUI.chainRoute<chain.routes.length?overviewUI.chainRoute:longest;
  const route=chain.routes[index],nets=[...new Set([chain.target_net,...route.steps.flatMap(s=>[s.net,s.input_net,s.output_net])].filter(Boolean))];
  const subset={...chain,nets,routes:[route],branches:(chain.branches || []).filter(b=>nets.includes(b.from.net))};
  subset.components=[...new Set(routingPins(subset).map(p=>p.reference))];return subset;
}
function routingPins(chain){
  const pins=[...(chain.loads || chain.endpoints || [])];
  for(const route of chain.routes || [])for(const step of route.steps) pins.push(...(step.pins || []),...(step.input_pins || []),...(step.output_pins || []),...(step.control_pins || []));
  for(const edge of [...(chain.series || []),...(chain.branches || [])])pins.push(edge.from,edge.to);
  const seen=new Set();return pins.filter(p=>{const key=p.reference+':'+p.pin;if(seen.has(key))return false;seen.add(key);return true;});
}
function routingViewerGroup(group){
  const chain=overviewUI.chainView && viewedRoutingChain();
  return chain?{...group,id:group.id+':'+chain.id,components:chain.components,issue_ids:[],chain_targets:routingPins(chain)}:group;
}
function showRoutingPin(pin){
  overviewUI.chainView=true;overviewUI.chainPin=pin.reference+':'+pin.pin;overviewUI.target=0;overviewUI.sheetFocus=null;
  const group=currentViewerGroup();
  if(group)renderIssueViewer(group);
  $('issue-viewer')?.scrollIntoView({behavior:'smooth',block:'center'});
}
function renderRoutingExplorer(parent){
  const chains=routingChains();if(!chains.length)return;
  if(!selectedRoutingChain()){overviewUI.chainId=(chains.find(c=>c.kind==='power' && c.loads.some(p=>p.role==='radio controller')) || chains[0]).id;overviewUI.chainRoute=null;overviewUI.chainView=false;overviewUI.chainPin=null;}
  const section=node('details','routing-explorer');section.id='routing-explorer';section.open=Boolean(overviewUI.chainOpen);section.addEventListener('toggle',()=>overviewUI.chainOpen=section.open);section.append(node('summary','','Explore circuit paths'));
  section.append(node('span','kicker','FOLLOW THE CIRCUIT'),node('h2','','What goes where'),node('p','field-help','Follow the saved connections through power stages, series parts and receiving pins. Part roles are inferred from the declared design; Vector uses the datasheets to check the arrangement.'));
  const picker=node('select');picker.setAttribute('aria-label','Circuit path');
  for(const c of [...chains].sort((a,b)=>(a.kind==='signal')-(b.kind==='signal'))){const option=node('option','',c.title);option.value=c.id;picker.append(option);}
  picker.value=overviewUI.chainId;
  picker.addEventListener('change',()=>{overviewUI.chainId=picker.value;overviewUI.chainRoute=null;overviewUI.chainView=false;overviewUI.chainPin=null;const old=$('routing-explorer');const holder=node('div');renderRoutingExplorer(holder);old.replaceWith(holder.firstChild);const group=currentViewerGroup();if(group)renderIssueViewer(group);});
  section.append(picker);const chain=selectedRoutingChain();section.append(node('p','',chain.explanation));
  const pinButton=(pin)=>{const b=node('button','chain-pin',`${pin.reference} pin ${pin.pin} · ${pin.name || pin.value} → ${pin.net}`);b.type='button';b.addEventListener('click',()=>showRoutingPin(pin));return b;};
  if(chain.routes?.length){
    const longest=chain.routes.reduce((best,r,index)=>r.steps.length>chain.routes[best].steps.length?index:best,0);
    if(!Number.isInteger(overviewUI.chainRoute) || overviewUI.chainRoute>=chain.routes.length)overviewUI.chainRoute=longest;
    if(chain.routes.length>1){
      const origins=node('select');origins.setAttribute('aria-label','Possible source path');
      chain.routes.forEach((r,index)=>{const labels=r.steps.map(s=>s.reference || s.pins?.[0]?.reference || 'source');const option=node('option','',labels.join(' → ') || 'No source identified');option.value=String(index);origins.append(option);});origins.value=String(overviewUI.chainRoute);
      origins.addEventListener('change',()=>{overviewUI.chainRoute=Number(origins.value);overviewUI.chainPin=null;overviewUI.target=0;overviewUI.sheetFocus=null;const old=$('routing-explorer'),holder=node('div');renderRoutingExplorer(holder);old.replaceWith(holder.firstChild);const group=currentViewerGroup();if(overviewUI.chainView && group)renderIssueViewer(group);});section.append(origins);
    }
    const route=chain.routes[overviewUI.chainRoute],flow=node('ol','chain-flow');
    for(const step of route.steps){
      const card=node('li','chain-stage');card.append(node('strong','',step.kind==='external_connection'?'External connection':`${step.reference} · ${step.value}`),node('p','',step.why));
      for(const pin of [...(step.pins || []),...(step.input_pins || []),...(step.output_pins || [])])card.append(pinButton(pin));
      if(step.control_pins?.length){card.append(node('small','','Settings and status connections'));for(const pin of step.control_pins)card.append(pinButton(pin));}
      if(step.return_nets?.length)card.append(node('small','',`Return connection: ${step.return_nets.join(', ')}`));flow.append(card);
    }
    section.append(flow);
    if(route.source_status!=='external_connection')section.append(node('p','review-notice',route.question));
    else section.append(node('p','field-help','This identifies the external connector, not the device attached to it or its measured voltage.'));
  }
  const targets=node('details','chain-connections');targets.open=!chain.routes?.length;
  targets.append(node('summary','',`${chain.loads?'Where it ends':'Connected endpoints'} · ${(chain.loads || chain.endpoints || []).length} pins`));
  for(const pin of chain.loads || chain.endpoints || []){targets.append(pinButton(pin));targets.append(node('small','',`${pin.role} · ${pin.electrical_type} · ${pin.board_matches?'same net on schematic and PCB':'PCB match not confirmed'}`));}section.append(targets);
  if(chain.series?.length){const row=node('div','chain-branches');row.append(node('h3','','Parts along the signal path'));for(const edge of chain.series){row.append(node('p','',`${edge.reference} (${edge.value}) links ${edge.from.net} to ${edge.to.net}. ${edge.kind==='capacitor between nets'?'This is possible AC coupling through a capacitor; the nets are separate for DC.':edge.why}`),pinButton(edge.from),pinButton(edge.to));}section.append(row);}
  if(chain.branches?.length){
    const side=node('details','chain-branches');side.append(node('summary','',`Side connections · ${chain.branches.length}`));
    for(const branch of chain.branches){const card=node('div');card.append(node('strong','',`${branch.reference} · ${branch.value} · ${branch.kind}`),node('p','',`${branch.from.net} → ${branch.to.net}. ${branch.why}`),pinButton(branch.from));side.append(card);}section.append(side);
  }
  const show=node('button','secondary','Show this path on the schematic / PCB');show.type='button';show.addEventListener('click',()=>{overviewUI.chainView=true;overviewUI.chainPin=null;overviewUI.target=0;overviewUI.sheetFocus=null;const group=currentViewerGroup();if(group)renderIssueViewer(group);$('issue-viewer')?.scrollIntoView({behavior:'smooth',block:'center'});});section.append(show);
  section.append(node('p','field-help','These are circuit connections and inferred functions. Actual current flow, switch state, voltage, impedance and noise need datasheets, operating context or measurements.'));
  parent.append(section);
}
