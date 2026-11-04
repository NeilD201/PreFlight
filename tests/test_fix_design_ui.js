// Coordinated-fix entry gating: only offered for automatically fixable
// findings, never for no-change/reference/order settings. Also guards
// against global-scope collisions between file_fixes.js and fix_design.js.
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
    reviewInstruction:text=>text,node:(tag,_class,text='')=>new Element(tag,text),app:{snapshot:{report_id:'r1'},project:{workspace:{},file_fix_options:{R1:{mode:'automatic'}},action_plan:{actions:[]}},config:{key_configured:true},busy:false},
    designUI:{chatBusy:false},openStepChat:(...args)=>calls.push({path:'investigate',args}),openSettings:()=>calls.push({path:'settings'}),
    api:async(path,data)=>{calls.push({path,data});return responder(path,data,body);},updateSavedProject:()=>{calls.push({path:'update-review'});}});
  vm.runInContext(fs.readFileSync('web/file_fixes.js','utf8'),context);
  vm.runInContext(fs.readFileSync('web/fix_design.js','utf8'),context);
  return {context,body,calls,run:code=>vm.runInContext(code,context)};
}
(async()=>{
  const shown=setup(()=>({}));
  assert.equal(typeof shown.run('coordinatedFixEntry'), 'function','entry point exists alongside file_fixes.js globals');
  const entry=shown.run("coordinatedFixEntry({id:'R1',title:'Value mismatch'})");
  assert.ok(entry,'automatic finding offers a coordinated fix');
  assert.equal(descendants(entry).find(e=>e.tag==='button').text,'Plan coordinated fix');
  for(const [group,why] of [
    [{id:'R1',no_change_basis:'x'},'no-change findings must not offer edits'],
    [{id:'R1',reference_only:true},'reference-only advice must not offer edits'],
  ]){
    assert.equal(shown.run(`coordinatedFixEntry(${JSON.stringify(group)})`),null,why);
  }
  const order=setup(()=>({}));
  order.context.app.project.action_plan={actions:[{group_id:'R1',action_kind:'order_setting'}]};
  assert.equal(order.run("coordinatedFixEntry({id:'R1'})"),null,'ordering requirements must not offer CAD repair');
  const manual=setup(()=>({}));
  manual.context.app.project.file_fix_options={R1:{mode:'investigate',message:'Needs evidence.'}};
  assert.equal(manual.run("coordinatedFixEntry({id:'R1'})"),null,'non-automatic findings use the investigation flow, not the coordinated plan');
  const cached=setup(()=>({}));
  cached.context.app.fixDesigns={report_id:'r1',items:[{id:'abc123',status:'approved',group_ids:['R1'],files_changed:['test.kicad_pcb'],message:'Approved.'}]};
  const withResume=cached.run("coordinatedFixEntry({id:'R1',title:'Value mismatch'})");
  const resumeBtn=descendants(withResume).find(e=>e.tag==='button' && String(e.text).startsWith('Resume'));
  assert.ok(resumeBtn,'a saved plan offers resume after reload');
  assert.match(resumeBtn.text,/approved/,'resume names the saved plan status');
  resumeBtn.listeners.click();
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.ok(cached.calls.some(c=>c.path==='/api/fix-design/abc123'),'resume fetches the saved record instead of preparing again');
  for(const [cache,why] of [
    [{report_id:'r1',items:[{id:'x',status:'approved',group_ids:['R9']}]},'no resume for other findings'],
    [{report_id:'other',items:[{id:'x',status:'approved',group_ids:['R1']}]},'no resume for another report'],
  ]){
    const scoped=setup(()=>({}));scoped.context.app.fixDesigns=cache;
    assert.ok(!descendants(scoped.run("coordinatedFixEntry({id:'R1'})")).some(e=>e.tag==='button' && String(e.text).startsWith('Resume')),why);
  }
  const prepareCalls=[];
  const blocked=setup((path,data)=>{
    if(path==='/api/fix-design/prepare'){prepareCalls.push(data);return prepareCalls.length===1
      ?{id:'f1',status:'blocked',group_ids:['R1'],message:'This review was analyzed from the uploaded copy board.zip, which Fix Design cannot edit. Link the live KiCad folder, run Check again.'}
      :{id:'f1',status:'validated',group_ids:['R1'],steps:[],files_changed:[],diffs:{},message:'Checked.'};}
    if(path==='/api/fix-design/reconnect')return {path:data.folder,files:[]};
    return {};
  });
  blocked.run("openFixDesign({id:'R1',title:'Value mismatch'})");
  await new Promise(resolve=>setTimeout(resolve,50));
  const dialog=blocked.body.children[0];
  assert.ok(dialog,'blocked prepare opens the fix dialog, not a bare error');
  const typed=descendants(dialog).find(e=>e.tag==='input' && String(e.placeholder||'').includes('type the project folder'));
  assert.ok(typed,'a typed folder path is offered when the native dialog can fail');
  const linkTyped=descendants(dialog).find(e=>e.tag==='button' && String(e.text).includes('Link typed folder'));
  assert.ok(linkTyped,'typed folder has an explicit link action');
  typed.value='/Volumes/Ext HDD/WenneBoard/PCB';
  await linkTyped.listeners.click();
  const reconnect=blocked.calls.find(c=>c.path==='/api/fix-design/reconnect');
  assert.equal(reconnect.data.folder,'/Volumes/Ext HDD/WenneBoard/PCB','typed paths with spaces reach reconnect unchanged');
  assert.equal(reconnect.data.report_id,'r1','reconnect targets the open review');
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.equal(prepareCalls.length,2,'a successful reconnect retries the fix preparation');
  // Synchronous prepare failure (upload-born review): the catch path must
  // offer the same typed-folder recovery, not a dead notice with no buttons.
  const syncFail=setup((path)=>{
    if(path==='/api/fix-design/prepare')throw new Error('This review was analyzed from the uploaded copy ffx5.zip, which Fix Design cannot edit. Link the live KiCad folder, run Check again.');
    return {};
  });
  syncFail.run("openFixDesign({id:'R1',title:'Value mismatch'})");
  await new Promise(resolve=>setTimeout(resolve,50));
  const deadEnd=syncFail.body.children[0];
  assert.ok(deadEnd,'synchronous prepare failure opens the dialog');
  const deadText=descendants(deadEnd).map(e=>e.text||'').join('\n');
  assert.ok(deadText.includes('ffx5.zip'),'the actionable message names the uploaded copy');
  const deadTyped=descendants(deadEnd).find(e=>e.tag==='input' && String(e.placeholder||'').includes('type the project folder'));
  assert.ok(deadTyped,'synchronous failure still offers a typed folder path');
  assert.ok(descendants(deadEnd).some(e=>e.tag==='button' && String(e.text).includes('Link typed folder')),'synchronous failure still offers the link action');
  console.log('fix_design UI gating: all assertions passed');
})().catch(error=>{console.error(error);process.exit(1);});
