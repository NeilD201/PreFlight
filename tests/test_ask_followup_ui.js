// A follow-up question about a saved review must be answered from that saved
// evidence through the lightweight chat path - never by re-running a full
// Vector review (the runaway that made analysis feel endless).
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class FakeElement{
  constructor(tag='div'){this.tag=tag;this.children=[];this.listeners={};this.dataset={};this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.checked=false;this.open=false;this.href='';this.childNodes=[{textContent:''}];this.classList={add(){},remove(){},toggle(){return false;},contains(){return false;}};this.style={};}
  set id(v){this._id=v;elements.set(v,this);}get id(){return this._id;}
  append(...items){this.children.push(...items);}prepend(...items){this.children.unshift(...items);}
  replaceChildren(...items){this.children=items;}before(){}after(){}insertBefore(){}remove(){}closest(){return null;}focus(){}blur(){}scrollIntoView(){}showModal(){this.open=true;}close(){this.open=false;}
  setAttribute(){}getAttribute(){return null;}removeAttribute(){}querySelector(){return new FakeElement();}querySelectorAll(){return[];}
  get parentElement(){if(!this._parent)this._parent=new FakeElement(this.tag+'-parent');return this._parent;}
  setAttributeNS(){}appendChild(item){this.children.push(item);return item;}
  addEventListener(name,fn){(this.listeners[name]=this.listeners[name]||[]).push(fn);}
  dispatch(name,event={}){return Promise.all((this.listeners[name]||[]).map(fn=>fn(event)));}
}
const elements=new Map();
const documentStub={getElementById(id){if(!elements.has(id)){const el=new FakeElement();el._id=id;elements.set(id,el);}return elements.get(id);},createElement(tag){return new FakeElement(tag);},createElementNS(ns,tag){return new FakeElement(tag);},createTextNode(text){return{textContent:text};},querySelector(){return new FakeElement();},querySelectorAll(){return[];}};
const requests=[];
const context=vm.createContext({console,document:documentStub,window:{},Node:FakeElement,AbortController,setTimeout,clearTimeout,
  matchMedia:()=>({matches:false,addEventListener(){},removeEventListener(){}}),
  setInterval:()=>0,clearInterval:()=>{},
  location:{search:'',href:'http://localhost/'},URLSearchParams,URL,
  fetch:async(url,options={})=>{let data=null;try{data=JSON.parse(options.body||'null');}catch{}requests.push({url,data});
    return{ok:true,status:200,json:async()=>url.endsWith('/api/config')?{csrf_token:'token',key_configured:true,default_path:'',model:'meta/muse-spark-1.3-contributor',ai_request_limits:{review:12,repair:20,chat:4,firmware:8,connection:2}}:{}};},
});
// Same script order as web/index.html (classic scripts only; livetwin is a module).
for(const file of ['web/workflow.js','web/command.js','web/design.js','web/file_fixes.js','web/fix_design.js','web/firmware.js','web/manufacturing.js','web/release.js','web/locations.js','web/overview.js','web/product-context.js','web/editor-connections.js','web/usage.js','web/app.js','web/breadboard.js','web/scanmath.js','web/livescan.js'])
  vm.runInContext(fs.readFileSync(file,'utf8').replace('initialize().catch(showError);',''),context,{filename:file});
vm.runInContext('initializeSimpleSetup=()=>{};initializeDesign=()=>{};initializeOverview=()=>{};refreshed=false;refresh=async()=>{refreshed=true;};',context);
(async()=>{
  await vm.runInContext('initialize()',context);
  const form=elements.get('ask-form');
  assert.ok(form,'Vector review panel wires an ask-about-this-run form');
  assert.ok((form.listeners.submit||[]).length>0,'Ask form has a submit handler');
  vm.runInContext("app.snapshot.report_id='rep-1';app.config.key_configured=true;$('followup-question').value='Why is R005 investigation-required?';",context);
  requests.length=0;
  await form.dispatch('submit',{preventDefault(){}});
  const chatCalls=requests.filter(r=>r.url==='/api/chat');
  assert.equal(requests.filter(r=>r.url==='/api/ask').length,0,'Follow-up must not trigger a full-review rethink request');
  assert.equal(chatCalls.length,1,'Exactly one request answers the follow-up');
  assert.equal(chatCalls[0].data.report_id,'rep-1','Answer is bound to the current saved review');
  assert.match(chatCalls[0].data.question,/R005/,'The user question is forwarded unchanged');
  assert.deepEqual(chatCalls[0].data.history,[],'No stale history is attached to a fresh follow-up');
  assert.equal(vm.runInContext('refreshed',context),true,'The view refreshes so the saved answer becomes visible');
  console.log('Ask follow-up: saved-evidence chat path, bound report, refreshed view passed');
  process.exit(0);
})().catch(e=>{console.error(e);process.exit(1);});
