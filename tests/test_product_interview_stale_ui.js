// Stale product-interview recovery: a 409 after re-analysis must offer an
// inline reload path, never a dead-end error loop.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
  constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.listeners={};this.attrs={};this.isConnected=true;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  addEventListener(type,fn){this.listeners[type]=fn;}
  setAttribute(k,v){this.attrs[k]=v;}
  showModal(){}
  get lastChild(){return this.children[this.children.length-1];}
  close(){this.isConnected=false;}
  remove(){this.isConnected=false;}
  focus(){}
  querySelector(sel){return this.querySelectorAll(sel)[0]||null;}
  querySelectorAll(sel){
    const out=[];
    const walk=e=>{for(const c of e.children||[]){
      if(sel==='button'&&c.tag==='button')out.push(c);
      if(sel==='[data-reload-review]'&&c.attrs&&c.attrs['data-reload-review'])out.push(c);
      walk(c);}};
    walk(this);return out;
  }
}
function descendants(root){return [root,...root.children.flatMap(descendants)];}
(async()=>{
  const body=new Element('body'),calls=[];
  const flags={reopened:false,refreshed:false};
  const context=vm.createContext({console,setTimeout,flags,
    document:{body,querySelector:()=>null,createTextNode:text=>new Element('text',text)},
    node:(tag,_class,text='')=>new Element(tag,text),
    showError:e=>{throw e;},
    refresh:async()=>{flags.refreshed=true;},
    app:{busy:false,view:'workspace',snapshot:{report_id:'r1'},
      project:{workspace:{design_context:{revision:1,product_context:{}}}},
      config:{key_configured:true}}});
  vm.runInContext(fs.readFileSync('web/product-context.js','utf8'),context);
  vm.runInContext('editProductContext=async()=>{flags.reopened=true;};reopened=0;',context);
  const apiCalls=[];
  vm.runInContext(`api=async(path,data)=>{${''}apiCalls.push(path);
    if(path==='/api/product-questions')return {questions:[{id:'experience',reference:'You',question:'Experience?',known:'k',hint:'h'}],answers:{},context_revision:2,unanswered:['experience']};
    throw new Error('Open the current project before saving an answer.');};`,context);
  // expose apiCalls for assertions
  context.apiCalls=apiCalls;
  const pending=vm.runInContext(`interviewProduct({},'scope','Use answers & review',false,'r1',1)`,context);
  await new Promise(resolve=>setImmediate(resolve));
  const dialog=body.children[0];
  assert.ok(dialog&&dialog.tag==='dialog','interview dialog opens');
  const field=descendants(dialog).find(e=>e.tag==='textarea');
  field.value='Beginner';
  const form=descendants(dialog).find(e=>e.tag==='form');
  form.listeners.submit({preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  await new Promise(resolve=>setImmediate(resolve));
  const reload=descendants(dialog).find(e=>e.attrs&&e.attrs['data-reload-review']);
  assert.ok(reload,'stale 409 offers an inline reload path instead of a dead-end error');
  assert.ok(!descendants(dialog).some(e=>e.text==='Retry investigation'));
  assert.equal(apiCalls.filter(p=>p==='/api/product-answers').length,1,'no repeated failing saves');
  reload.listeners.click();
  await new Promise(resolve=>setImmediate(resolve));
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(flags.refreshed,'reload refreshes the project first');
  assert.ok(flags.reopened,'reload reopens the interview on the current review');
  await pending;
  console.log('Stale product-interview recovery: 409 explains and reloads instead of looping passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
