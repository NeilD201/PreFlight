// Engineering forms must preserve edits and never spend AI credits on calculations.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.listeners={};this.value='';this.dataset={};}
  append(...items){this.children.push(...items);if(this.tag==='select' && !this.value)this.value=items[0]?.value || '';}
  replaceChildren(...items){this.children=items;}
  addEventListener(type,fn){this.listeners[type]=fn;}
  setAttribute(){}
  querySelectorAll(tags){return all(this).filter(e=>tags.split(',').includes(e.tag));}
  get elements(){return {namedItem:name=>all(this).find(e=>e.name===name)};}
}
function all(e){return [e,...e.children.flatMap(all)];}
const calls=[];let resolveRequest;
const calculation={key:'thermal:U1',section:'thermal',kind:'thermal',signature:'original',label:'U1',
  status:'checked',detail:'Old passing result',values:{loss:1},note:'Old case',fields:[{key:'loss',label:'Loss',unit:'W',minimum:0}]};
const home=new Element('div');
const context=vm.createContext({node:(tag,cls,text='')=>new Element(tag,text),$:()=>home,
  app:{snapshot:{report_id:'board'},busy:false,project:{verification_results:{revision:1,calculations:[calculation]}}},
  api:(path,data)=>{calls.push({path,data});return new Promise(resolve=>{resolveRequest=resolve});},refresh:async()=>{}});
vm.runInContext(fs.readFileSync('web/release.js','utf8'),context);
(async()=>{
  let form=vm.runInContext("calculationForm('thermal','scope')",context);
  const original=all(form).find(e=>e.tag==='input');original.value='2';
  all(form).find(e=>e.tag==='form').listeners.input();
  const restored=vm.runInContext("calculationForm('thermal','scope')",context);
  assert.equal(all(restored).find(e=>e.tag==='input').value,'2');
  assert.ok(all(restored).some(e=>e.textContent==='Inputs changed. Calculate to update the result.'));
  assert.ok(!all(restored).some(e=>e.textContent==='Old passing result'));
  const release=vm.runInContext("calculationForm('thermal')",context);
  assert.notEqual(all(restored).find(e=>e.tag==='input').id,all(release).find(e=>e.tag==='input').id);
  const saved=all(restored).find(e=>e.tag==='form');
  saved.listeners.submit({preventDefault(){}});saved.listeners.submit({preventDefault(){}});
  assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/verification');assert.equal(calls[0].data.values.loss,2);
  resolveRequest({revision:2});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(vm.runInContext('engineeringDrafts.size',context),0);
  const section=vm.runInContext('engineeringAnalysis()',context);
  const measure=all(section).find(e=>e.textContent==='Measure saved board');
  const pending=measure.listeners.click();await measure.listeners.click();
  assert.equal(calls.length,2);assert.equal(calls[1].path,'/api/engineering-geometry');
  resolveRequest({status:'measured'});await pending;
  context.app.project.board_checks={signal_geometry:{status:'measured',straight_tracks:2,parallel_count:0}};
  const measured=vm.runInContext('engineeringAnalysis()',context);
  assert.ok(all(measured).some(e=>e.textContent.includes('zero gaps does not prove')));
  assert.ok(!all(measured).some(e=>e.textContent==='Passed'));
  // A context change must discard drafts tied to the previous calculation signature.
  vm.runInContext("engineeringDrafts.set('board:original',{values:{loss:2}})",context);
  calculation.signature='changed';context.app.project.check_scope={questions:[],checks:[],signature:'new',context_revision:2};
  vm.runInContext('renderCheckScope()',context);
  assert.equal(vm.runInContext('engineeringDrafts.size',context),0);
  console.log('Engineering UI: preserved drafts, stale-result removal, unique fields, duplicate guards and local-only requests passed');
})().catch(error=>{console.error(error);process.exitCode=1});
