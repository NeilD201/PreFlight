// The workspace header must name the loaded KiCad files and whether they are
// a live link or uploaded copies — never just a component count.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function makeElement(id){
  return {id,hidden:false,value:'',textContent:'',open:false,children:[],
    append(...items){this.children.push(...items);},
    replaceChildren(...items){this.children=items;},
    setAttribute(){},addEventListener(){},focus(){},querySelector(){return makeElement(id+'-q');}};
}
const elements=new Map();
const context=vm.createContext({console,
  document:{getElementById:id=>{if(!elements.has(id))elements.set(id,makeElement(id));return elements.get(id);},
    createElement:tag=>makeElement(tag),body:makeElement('body')},
  window:{},localStorage:{getItem:()=>null,setItem:()=>{}}});
vm.runInContext(fs.readFileSync('web/workflow.js','utf8'),context);
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace('initialize().catch(showError);',''),context);
vm.runInContext(`
  restoreInput=()=>{};renderRevisionProgress=()=>{};renderReadiness=()=>{};
  renderAssembly=()=>{};renderParts=()=>{};loadBaselines=()=>Promise.resolve();
  renderDesign=()=>{};renderAssemblyDownloads=()=>{};renderOverview=()=>{};
  renderSchematicStatus=()=>{};renderSelectedDetails=()=>{};
  renderChatHome=()=>{};renderCheckScope=()=>{};
  showError=()=>{};list=()=>makeElementStub();table=()=>makeElementStub();note=()=>makeElementStub();
  function makeElementStub(){return {children:[],listeners:{},append(){},replaceChildren(){},addEventListener(e,f){this.listeners[e]=f;}};}
  node=(tag,cls,text='')=>{const e=makeElementStub();e.tag=tag;e.text=text;return e;};
  app.adding=false;
`,context);
function subtitleFor(project){
  elements.clear();
  context.project=project;
  vm.runInContext('app.project=project;renderProject()',context);
  return elements.get('project-subtitle').textContent;
}
const live={provenance:{project_name:'Macropod',created:'2026-11-01T10:00:00Z'},
  components:{R1:{},R2:{}},selected_files:{schematic:'macro.kicad_sch',pcb:'macro.kicad_pcb'},
  workspace:{input_source:{path:'/boards/macro'}}};
const uploaded={provenance:{project_name:'Macropod',created:'2026-11-01T10:00:00Z'},
  components:{R1:{}},selected_files:{schematic:'macro.kicad_sch',pcb:'macro.kicad_pcb'},
  workspace:{input_source:{upload_name:'selected-project.zip'}}};
const liveText=subtitleFor(live);
assert.ok(liveText.includes('macro.kicad_sch'),'Live schematic filename must show');
assert.ok(liveText.includes('macro.kicad_pcb'),'Live PCB filename must show');
assert.ok(liveText.includes('live files'),'Live link must be labelled');
assert.ok(liveText.includes('2 components'),'Component count must stay');
const upText=subtitleFor(uploaded);
assert.ok(upText.includes('macro.kicad_sch + macro.kicad_pcb'),'Upload filenames must show');
assert.ok(upText.includes('uploaded copies'),'Copies must be labelled as copies, never as live');
assert.ok(!upText.includes('live files'),'Copies must not claim a live link');
const emptyText=subtitleFor({provenance:{},components:{},selected_files:{},workspace:{}});
assert.ok(!emptyText.includes('undefined'),'Missing files must not leak undefined');
console.log('Project header: loaded filenames with live/copy labels passed');

// One intake: Browse links files and every linked file is listed while adding.
vm.runInContext(`clearSourceSelections=()=>{};inputMode=()=>{};`,context);
elements.clear();
vm.runInContext(`connectOriginalFiles({path:'/boards/macro',name:'macro',schematic:'macro.kicad_sch',pcb:'macro.kicad_pcb',
  sheets:['macro.kicad_sch','psu.kicad_sch'],
  files:{'.kicad_pro':'/boards/macro/macro.kicad_pro','.kicad_pcb':'/boards/macro/macro.kicad_pcb','.kicad_sch':'/boards/macro/macro.kicad_sch'}})`,context);
const listed=elements.get('connected-file-list').children.map(c=>c.text).join('\n');
for(const name of ['macro.kicad_pro','macro.kicad_pcb','macro.kicad_sch','psu.kicad_sch']){
  assert.ok(listed.includes(name),'Linked file visible while adding: '+name);
}
assert.ok(elements.get('connected-project-hint').textContent.includes('Connected'),'Link state is stated, not implied');
console.log('Link intake: Browse links all sheets and lists them visibly passed');

// Saved route auto-links with no dialog; without one, staging stays quiet
// and the Link button falls back to the native picker exactly once.
(async()=>{
vm.runInContext(`clearSourceSelections=()=>{};inputMode=()=>{};showError=(e)=>{thrownError=e;};var thrownError=null;
app.config={linked_files:{path:'/boards/macro'}};apiCalls=[];
api=async (path,data)=>{apiCalls.push(path);
  if(String(path).endsWith('/api/link-local-paths')){
    if(data&&data.directory==='/boards/macro')return linkedFixture;
    throw new Error('unknown route');
  }
  await gate;return linkedFixture;};`,context);
elements.clear();
let releasePicker;
const gate=new Promise(resolve=>{releasePicker=resolve;});
context.gate=gate;
context.linkedFixture={path:'/boards/macro',name:'macro',schematic:'macro.kicad_sch',pcb:'macro.kicad_pcb',
  sheets:['macro.kicad_sch','psu.kicad_sch'],
  files:{'.kicad_pro':'/boards/macro/macro.kicad_pro','.kicad_pcb':'/boards/macro/macro.kicad_pcb','.kicad_sch':'/boards/macro/macro.kicad_sch'}};
const autoLinked=await vm.runInContext(`tryAutoLink(['macro.kicad_sch','psu.kicad_sch'])`,context);
assert.equal(autoLinked,true,'Saved route links dropped names with no dialog');
assert.equal(vm.runInContext(`apiCalls.length`,context),1,'Auto-link makes one backend call');
assert.equal(vm.runInContext(`apiCalls[0]`,context),'/api/link-local-paths','Auto-link never touches the native picker');
const autoText=elements.get('connected-file-list').children.map(c=>c.text).join('\n');
for(const name of ['macro.kicad_pro','macro.kicad_pcb','macro.kicad_sch','psu.kicad_sch']){
  assert.ok(autoText.includes(name),'Auto-link ends with real path listed: '+name);
}
console.log('Drop intake: saved route auto-links with no dialog passed');
vm.runInContext(`app.config={};apiCalls=[];`,context);
elements.clear();
vm.runInContext(`stageDroppedNames(['macro.kicad_sch','psu.kicad_sch'])`,context);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(vm.runInContext(`apiCalls.length`,context),0,'Staging names must not fire anything on its own');
const staged=elements.get('connected-file-list').children.map(c=>c.text).join('\n');
assert.ok(staged.includes('macro.kicad_sch'),'Dropped name visible while adding');
assert.ok(staged.includes('waiting to link'),'Pending state says what happens next');
const linkButton=elements.get('connected-file-list').children.find(c=>c.tag==='button');
assert.ok(linkButton,'A Link button finishes the handoff');
const clicking=linkButton.listeners.click();
await new Promise(resolve=>setImmediate(resolve));
assert.equal(vm.runInContext(`apiCalls.length`,context),1,'The Link button makes one backend call');
assert.equal(vm.runInContext(`apiCalls[0]`,context),'/api/choose-local-files','Without a route the Link button opens the picker exactly once');
assert.ok(String(linkButton.textContent||'').includes('Waiting for the Mac file dialog'),'Button states the wait while picking');
releasePicker();
await clicking;
const linked=elements.get('connected-file-list').children.map(c=>c.text).join('\n');
for(const name of ['macro.kicad_pro','macro.kicad_pcb','macro.kicad_sch','psu.kicad_sch']){
  assert.ok(linked.includes(name),'Drop-to-link ends with real path listed: '+name);
}
assert.ok(!linked.includes('waiting to link'),'Pending state cleared after linking');
assert.equal(vm.runInContext(`thrownError&&thrownError.message||null`,context),null,'Drop-to-link must not swallow errors');
console.log('Drop intake: staged names, explicit Link button, picker fallback passed');
vm.runInContext(`stageDroppedNames(['a.kicad_sch'],'These files are not in /boards/macro: a.kicad_sch.')`,context);
assert.ok(elements.get('connected-project-hint').textContent.includes('/boards/macro'),'The staging hint shows why auto-link failed');
console.log('Drop intake: auto-link failure reason is shown passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
