// The one-click fix must be an explicit choice and must stop on failed checks.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
  constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.listeners={};this.isConnected=true;this.checked=false;this.style={};}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  addEventListener(type,fn){this.listeners[type]=fn;}
  setAttribute(){}
  showModal(){}
  close(){this.isConnected=false;this.listeners.close?.();}
  remove(){this.isConnected=false;}
}
function descendants(root){return [root,...root.children.flatMap(descendants)];}
function setup(responder){
  const body=new Element('body'),calls=[];
  const context=vm.createContext({console,setTimeout,document:{body,createTextNode:text=>new Element('text',text)},
    reviewInstruction:text=>text,node:(tag,_class,text='')=>new Element(tag,text),app:{snapshot:{report_id:'r1'},project:{workspace:{},file_fix_options:{R1:{mode:'automatic'}}},config:{key_configured:true},busy:false},
    designUI:{chatBusy:false},openStepChat:(...args)=>calls.push({path:'investigate',args}),openSettings:()=>calls.push({path:'settings'}),
    api:async(path,data)=>{calls.push({path,data});return responder(path,data,body);},updateSavedProject:()=>{calls.push({path:'update-review'});}});
  vm.runInContext(fs.readFileSync('web/file_fixes.js','utf8'),context);
  return {context,body,calls,run:code=>vm.runInContext(code,context)};
}
const ready={id:'fix1',status:'ready',summary:'Connect U1 ground',file:'board.kicad_pcb',checks:['DRC passed'],backup:'backup'};
(async()=>{
  const gated=setup(()=>({}));
  assert.equal(gated.run("fileFixEntry({id:'R1'},{disposition:'no_change'})"),null,
    'Do not offer a file edit when the reviewed result says no change is needed');
  gated.context.app.project.workspace.file_fixes=[{group_id:'R1',status:'blocked'}];
  assert.equal(gated.run("fileFixEntry({id:'R1'},{disposition:'reference_only',reference_only:true})"),null,
    'Do not resurrect a repair button from an old blocked plan for reference-only advice');
  assert.equal(descendants(gated.run("fileFixEntry({id:'R1'})")).find(e=>e.tag==='button').text,'Fix this issue');
  assert.equal(gated.run("fileFixEntry({id:'R1'},{action_kind:'order_setting'})"),null,
    'Manufacturer ordering instructions must not offer a CAD repair');
  gated.context.app.project.action_plan={actions:[{group_id:'R1',action_kind:'order_setting'}]};
  assert.equal(gated.run("fileFixEntry({id:'R1'})"),null,'Other entry points must use the current action');
  gated.run("openFixWorkflow({id:'R1'})");
  assert.equal(gated.body.children.length,0,'Ordering requirements must not open a repair investigation');
  assert.equal(gated.calls.length,0,'Ordering requirements must not spend AI credits');
  let resolveInvestigation;
  const unsupported=setup(()=>new Promise(resolve=>resolveInvestigation=resolve));
  unsupported.context.app.project.file_fix_options={R1:{mode:'investigate',message:'No antenna endpoint identified.'}};
  const entry=unsupported.run("fileFixEntry({id:'R1',title:'Antenna path'})");
  assert.equal(descendants(entry).find(e=>e.tag==='button').text,'Fix this issue');
  assert.ok(descendants(entry).some(e=>e.text.includes('ask for your approval')));
  descendants(entry).find(e=>e.text==='Fix this issue').listeners.click();
  const investigationDialog=unsupported.body.children[0];
  unsupported.run("openFixWorkflow({id:'R1',title:'Antenna path'})");
  assert.equal(unsupported.body.children.length,1,'Repeated clicks must not stack repair dialogs');
  assert.equal(unsupported.calls[0].path,'/api/step-help');
  assert.equal(unsupported.calls[0].data.group_id,'R1');
  assert.ok(investigationDialog.isConnected);
  assert.ok(descendants(investigationDialog).some(e=>e.text==='Investigating this issue…'));
  investigationDialog.close();
  unsupported.run("openFixWorkflow({id:'R1'})");
  assert.equal(unsupported.calls.length,1,'Reopening must reuse a pending investigation');
  resolveInvestigation({status:'ai',answer:'The saved path needs further evidence.'});
  await new Promise(resolve=>setImmediate(resolve));
  const reopened=unsupported.body.children.at(-1);
  assert.ok(reopened.isConnected);
  assert.ok(descendants(reopened).some(e=>e.text==='The saved path needs further evidence.'));
  assert.ok(!unsupported.calls.some(c=>c.path==='investigate'),'Must not open the separate step chat');
  const followup=descendants(reopened).find(e=>e.tag==='form' && e.children.some(c=>c.text==='Ask follow-up'));
  const field=descendants(followup).find(e=>e.tag==='textarea');field.value='Which net?';
  followup.listeners.submit({preventDefault(){}});
  assert.equal(unsupported.calls[1].data.question,'Which net?');
  assert.ok(unsupported.calls[1].data.history.some(m=>m.text==='The saved path needs further evidence.'));
  resolveInvestigation({status:'ai',answer:'Use the saved RF output net.'});await new Promise(resolve=>setImmediate(resolve));
  assert.ok(descendants(reopened).some(e=>e.text==='Use the saved RF output net.'));
  reopened.close();
  unsupported.context.app.project.workspace.design_context={revision:1};
  unsupported.run("openFixWorkflow({id:'R1'})");
  assert.equal(unsupported.calls.length,3,'Changed intent must not reuse a stale repair investigation');
  resolveInvestigation({status:'ai',answer:'Updated for the new intent'});await new Promise(resolve=>setImmediate(resolve));
  const retrying=setup(()=>({status:'failed',message:'Service unavailable',detail:'HTTP 503'}));
  retrying.context.app.project.file_fix_options={};retrying.run("openFixWorkflow({id:'R1'})");
  await new Promise(resolve=>setImmediate(resolve));
  const retryDialog=retrying.body.children[0];
  assert.ok(retryDialog.isConnected);
  const retry=descendants(retryDialog).find(e=>e.text==='Retry investigation');
  retry.listeners.click();retry.listeners.click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(retrying.calls.length,2,'A second retry click must not duplicate an active request');
  assert.equal(retrying.calls[1].data.history.length,0,'Failed answers must not enter the next request history');
  const noKey=setup(()=>{throw Error('Missing key must not send evidence');});
  const antenna=setup(()=>({status:'ai',answer:'Additional analysis'}));
  antenna.context.app.project.file_fix_options={R1:{mode:'investigate',assessment:{
    title:'Antenna design cannot be applied automatically',observations:['RF_OUT: 0.93 mm of drawn tracks'],
    blockers:['No RF routing editor'],next_step:'Establish a checked layout'}}};
  antenna.run("openFixWorkflow({id:'R1'})");
  assert.equal(antenna.calls.length,0,'A blocked assessment must not trigger a speculative AI repair');
  assert.ok(descendants(antenna.body).some(e=>e.text.includes('These measurements start the investigation.')));
  assert.ok(descendants(antenna.body).some(e=>e.text==='RF_OUT: 0.93 mm of drawn tracks'));
  const antennaForm=descendants(antenna.body).find(e=>e.tag==='form' && e.children.some(c=>c.text==='Ask follow-up'));
  descendants(antennaForm).find(e=>e.tag==='textarea').value='What was measured?';
  antennaForm.listeners.submit({preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(antenna.calls[0].path,'/api/step-help');
  assert.ok(!antenna.calls.some(c=>c.path.startsWith('/api/file-fix/')));
  noKey.context.app.project.file_fix_options={};noKey.context.app.config.key_configured=false;
  noKey.run("openFixWorkflow({id:'R1'})");
  assert.equal(noKey.calls.length,0);
  assert.ok(descendants(noKey.body).some(e=>e.text==='Connect Vector'));
  let resolveOld;
  const old=setup(()=>new Promise(resolve=>resolveOld=resolve));old.context.app.project.file_fix_options={};
  old.run("openFixWorkflow({id:'R1'})");old.context.app.snapshot.report_id='r2';resolveOld({status:'ai',answer:'Old answer'});
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(!descendants(old.body).some(e=>e.text==='Old answer'));
  assert.ok(descendants(old.body).some(e=>e.text.includes('The review changed.')));
  let resolveStaleRetry;
  const staleRetry=setup(()=>new Promise(resolve=>resolveStaleRetry=resolve));staleRetry.context.app.project.file_fix_options={};
  staleRetry.run("openFixWorkflow({id:'R1'})");
  await new Promise(resolve=>setImmediate(resolve));
  resolveStaleRetry({status:'failed',message:'AI did not finish the answer.'});
  await new Promise(resolve=>setImmediate(resolve));
  const staleRetryDialog=staleRetry.body.children[0];
  assert.ok(descendants(staleRetryDialog).some(e=>e.text==='Retry investigation'));
  staleRetry.context.app.snapshot.report_id='r2';
  descendants(staleRetryDialog).find(e=>e.text==='Retry investigation').listeners.click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(staleRetry.calls.length,1,'A stale retry must not send another request');
  assert.ok(descendants(staleRetryDialog).some(e=>String(e.text).includes('Close this window and reopen')),'A stale retry must explain instead of closing silently');
  assert.ok(!descendants(staleRetryDialog).some(e=>e.text==='Retry investigation'),'A stale investigation offers no retry loop');
  const t=setup(path=>path.endsWith('/prepare')?ready:path.endsWith('/apply')?{status:'applied',file:'/project/board.kicad_pcb'}:{});
  const control=t.run("fileFixControl({id:'R1'})");
  const button=descendants(control).find(e=>e.text==='Apply fix'),check=descendants(control).find(e=>e.tag==='input');
  assert.equal(button.text,'Apply fix');assert.equal(button.disabled,true);
  button.listeners.click();assert.equal(t.calls.length,0,'Unchecked confirmation must never start a fix');
  check.checked=true;check.listeners.change();assert.equal(button.disabled,false);
  button.listeners.click();await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(t.calls.slice(0,2).map(c=>c.path),['/api/file-fix/prepare','/api/file-fix/apply']);
  assert.equal(t.calls[1].data.closed_in_kicad,true);
  assert.ok(descendants(t.body).some(e=>e.tag==='button' && e.text==='Update review'));
  assert.ok(descendants(t.body).some(e=>e.text.includes('Reopen board.kicad_pcb')));
  assert.ok(descendants(t.body).some(e=>e.text==='Undo this fix'));
  const blocked=setup(()=>({status:'blocked',message:'New clearance violation'}));
  const design={title:'Design repair',diagnosis:'Saved path checked',approach:'Use the documented pattern',changes:['Place the reference'],assumptions:[],validation:['Run DRC'],limitations:['Measure RF'],requirements:[],sources:[],questions:[{id:'case',question:'What enclosure?',why_needed:'Metal affects antenna performance.'}]};
  const staged=setup(path=>path.endsWith('/prepare')?{id:'design1',status:'awaiting_design_approval',design,edit_request:'Repair this'}:{});
  await staged.run("openFileFix({id:'R1'},null,false,'Repair this',true)");
  assert.equal(staged.calls[0].data.investigate_design,true);
  assert.ok(descendants(staged.body).some(e=>e.text==='Update the design with my answers'));
  assert.ok(!descendants(staged.body).some(e=>e.text==='Approve approach and prepare edits'));
  assert.ok(!staged.calls.some(c=>c.path.endsWith('/apply')));
  const approvalStage=setup(()=>({id:'design2',status:'awaiting_design_approval',design:{...design,questions:[]}}));
  await approvalStage.run("openFileFix({id:'R1'},null,false,'Repair this',true)");
  assert.ok(descendants(approvalStage.body).some(e=>e.text==='Approve approach and prepare edits'));
  assert.ok(!approvalStage.calls.some(c=>c.path.endsWith('/continue') || c.path.endsWith('/apply')));
  const batchPlan={...ready,kind:'edit_batch',requires_approval:true,change_summary:['Move R1 to (10, 12) mm'],previews:['before-front.svg','after-front.svg'],warnings:[]};
  const batch=setup(path=>path.endsWith('/prepare')?batchPlan:path.endsWith('/apply')?{status:'applied',file:'/project/board.kicad_pcb'}:{});
  await batch.run("openFileFix({id:'R1'},null,true,'Move R1 and reroute')");
  assert.ok(!batch.calls.some(c=>c.path.endsWith('/apply')),'A batch must never inherit automatic apply');
  const approve=descendants(batch.body).find(e=>e.text==='Approve and apply changes');
  assert.equal(approve.disabled,true);
  const approval=descendants(batch.body).find(e=>e.tag==='input');approval.checked=true;approval.listeners.change();
  approve.listeners.click();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(batch.calls.find(c=>c.path.endsWith('/apply')).data.approve_plan,'fix1');
  assert.ok(descendants(batch.body).some(e=>e.text==='Changes made'));
  await blocked.run("openFileFix({id:'R1'},null,true)");
  assert.equal(blocked.calls.length,2); // prepare, then refresh current project
  assert.ok(!blocked.calls.some(c=>c.path.endsWith('/apply')));
  const preview=setup(path=>path.includes('/file-fix/')?ready:{});
  await preview.run("openFileFix({id:'R1'},'fix1')");
  assert.ok(!preview.calls.some(c=>c.path.endsWith('/apply')),'Viewing a saved edit must not apply it');
  const closed=setup((_path,_data,body)=>{body.children[0].close();return ready;});
  await closed.run("openFileFix({id:'R1'},null,true)");
  assert.ok(!closed.calls.some(c=>c.path.endsWith('/apply')),'Closing before validation ends must stop automatic application');
  const changed=setup(path=>{if(path.endsWith('/prepare'))changed.context.app.snapshot.report_id='r2';return ready;});
  await changed.run("openFileFix({id:'R1'},null,true)");
  assert.ok(!changed.calls.some(c=>c.path.endsWith('/apply')));
  const stale=setup(()=>{throw Error('Must not act on a changed report');});
  stale.run("openFixWorkflow({id:'R1'})");
  const staleDialog=stale.body.children[0],staleCheck=descendants(staleDialog).find(e=>e.tag==='input');
  staleCheck.checked=true;staleCheck.listeners.change();stale.context.app.snapshot.report_id='r2';
  descendants(staleDialog).find(e=>e.text==='Apply fix').listeners.click();
  assert.equal(stale.calls.length,0);
  assert.ok(descendants(staleDialog).some(e=>e.text==='Review changed'));
  const preparations=[];
  const folder=setup(path=>{
    if(path.endsWith('/choose-local-folder'))return{path:'/proj'};
    if(path.endsWith('/reconnect'))return{};
    if(path.endsWith('/prepare')){preparations.push(path);return preparations.length>1?ready:{status:'blocked',message:'Connect the saved KiCad folder first.'};}
    if(path==='/api/project')return{};
    return{};
  });
  await folder.run("openFileFix({id:'R1'},null,true)");
  await new Promise(resolve=>setImmediate(resolve));
  const folderDialog=folder.body.children[0];
  assert.ok(descendants(folderDialog).some(e=>e.text==='Connect the saved KiCad folder first.'),'Blocked preparation must show the folder reason');
  const connect=descendants(folderDialog).find(e=>e.tag==='button' && e.text==='Connect folder…');
  assert.ok(connect,'A blocked folder preparation must offer reconnect, not a bare retry loop');
  await connect.listeners.click();
  await new Promise(resolve=>setImmediate(resolve));
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(folder.calls.some(c=>c.path.endsWith('/reconnect')),'Reconnect must verify the chosen folder');
  assert.equal(folder.calls.filter(c=>c.path.endsWith('/prepare')).length,2,'Reconnect must retry the same preparation');
  const resumed=setup(()=>({}));
  resumed.context.app.project.file_fix_options={R1:{mode:'investigate',message:'Needs evidence.'}};
  resumed.context.app.project.workspace.file_fixes=[{group_id:'R1',status:'awaiting_design_approval'}];
  resumed.run("openFixWorkflow({id:'R1',title:'LED'})");
  assert.equal(resumed.calls.filter(c=>c.path==='/api/step-help').length,0,
    'A prepared record must not auto-fire a second investigation that 409s Continue');
  assert.ok(descendants(resumed.body.children[0]).some(e=>e.text==='Continue repair'),
    'The prepared record must still offer Continue repair');
  console.log('Fix this: explicit choice, automatic apply after checks, failed checks, close, changed review, resumption without duplicate investigation, and completion instructions passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
