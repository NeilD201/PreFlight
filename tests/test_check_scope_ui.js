const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
  constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.listeners={};this.value='';this.disabled=false;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  addEventListener(type,fn){this.listeners[type]=fn;}
  setAttribute(){}
  querySelectorAll(tag){return all(this).filter(e=>e.tag===tag);}
  get elements(){return {namedItem:name=>all(this).find(e=>e.name===name)};}
}
function all(e){return [e,...e.children.flatMap(all)];}
const home=new Element('div');let calls=[],resolveSave;
const scope={signature:'design-a',context_revision:0,description:'Wired keyboard',
  questions:[{id:'environment:scope_radio:1234567890',feature:'radio',question:'Wireless used?',choices:['Yes','No'],answer:''}],
  checks:[{title:'Antenna',selection:'not_applicable',reason:'No radio identified',available:'Saved paths',limits:'No RF simulation',evidence:[]}]};
const context=vm.createContext({node:(tag,cls,text='')=>new Element(tag,text),$:id=>id==='checks-home'?home:null,
  app:{snapshot:{report_id:'board'},project:{check_scope:scope,workspace:{design_context:{revision:0}},before_ordering:{checks:[]}},busy:false},
  api:(path,data)=>{calls.push({path,data});return new Promise(resolve=>{resolveSave=resolve});},refresh:async()=>{}});
vm.runInContext(fs.readFileSync('web/release.js','utf8'),context);
vm.runInContext('renderCheckScope()',context);
assert.equal(calls.length,0);
assert.ok(all(home).some(e=>e.text==='Not selected'));
const form=all(home).find(e=>e.tag==='form');
const select=form.elements.namedItem(scope.questions[0].id);select.value='No';select.listeners.change();
vm.runInContext('renderCheckScope()',context);
assert.equal(all(home).find(e=>e.tag==='form'),form,'Polling must preserve unsaved input');
(async()=>{
  const pending=form.listeners.submit({preventDefault(){}});
  await form.listeners.submit({preventDefault(){}});
  assert.equal(calls.length,1,'Double submit must not save twice');
  assert.equal(calls[0].path,'/api/check-scope');
  assert.equal(calls[0].data.answers[scope.questions[0].id],'No');
  assert.equal(calls[0].data.signature,'design-a');
  assert.ok(!('ai' in calls[0].data));
  resolveSave({revision:1});await pending;
  context.app.project=null;vm.runInContext('renderCheckScope()',context);
  assert.ok(all(home).some(e=>e.text.includes('Open a project')));
  console.log('Check scope page: no paid request, preserved drafts, duplicate guard and empty state passed');
})().catch(error=>{console.error(error);process.exitCode=1});
