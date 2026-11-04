const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
  constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.listeners={};this.value='';this.hidden=false;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  addEventListener(kind,fn){this.listeners[kind]=fn;}
  setAttribute(){} querySelector(){return new Element('div');}
}
function all(node){return [node,...node.children.flatMap(all)];}
const byId={};
const FakeParams=class{constructor(o={}){this.o=o;}toString(){return Object.entries(this.o).map(([k,v])=>k+'='+v).join('&');}};
const navCalls=[];
const apiCalls=[];
const connected=[];
const context=vm.createContext({
  app:{busy:false,revision:5,config:{}},workflow:undefined,URLSearchParams:FakeParams,
  navigate:view=>navCalls.push(view),refresh:async()=>{navCalls.push('refresh');},
  node:(tag,cls,text='')=>new Element(tag,text),
  badge:(text,kind='')=>new Element('span',text),
  note:text=>new Element('p',text),
  document:{},showError:e=>{throw e;},
  connectOriginalFiles:linked=>{connected.push(linked);},
  $:id=>byId[id]||(byId[id]=new Element('div')),
  api:async(path,data)=>{
    apiCalls.push({path,data});
    if(String(path).startsWith('/api/demo-projects'))
      return {demos:[{name:'ModularKeyboard wrong',directory:'/d/wrong',files:['a.pro','a.pcb','a.sch']},{name:'ModularKeyboard correct',directory:'/d/correct',files:['b.pro','b.pcb','b.sch']}]};
    if(path==='/api/demo-open')return {path:'/work/'+data.name,name:data.name,schematic:'a.sch',pcb:'a.pcb',sheets:['a.sch'],files:{}};
    return {projects:[],items:[],total:0,offset:0,limit:30};
  }});
byId['history-search']=new Element('input');byId['history-project']=new Element('select');
byId['history-count']=new Element('span');byId['history-page-label']=new Element('p');
byId['history-list']=new Element('div');byId['history-view']=new Element('section');
vm.runInContext(fs.readFileSync('web/workflow.js','utf8'),context);
(async()=>{
  await vm.runInContext('loadLibrary()',context);
  const cards=all(byId['history-list']).filter(e=>e.tag==='article');
  assert.equal(cards.length,2,'Both bundled demo boards headline the Projects page');
  const openBtn=all(cards[1]).find(e=>e.tag==='button');
  openBtn.listeners.click();
  await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
  assert.ok(navCalls.includes('workspace'),'Opening a demo board navigates to the workspace');
  assert.equal(context.app.revision,-1,'Opening a demo board forces a project reload so the previous board cannot linger');
  const opened=apiCalls.find(c=>c.path==='/api/demo-open');
  assert.ok(opened,'Opening a demo board uses the working-copy endpoint');
  assert.equal(opened.data.name,'ModularKeyboard correct','Demo name is sent, never a client path');
  assert.equal(context.app.config.linked_files.path,'/work/ModularKeyboard correct','Linked working copy is stored in app config');
  assert.equal(connected.length,1,'Setup form connects the working copy');
  assert.equal(connected[0].path,'/work/ModularKeyboard correct','Connected path is the working copy');
  const firstButtons=all(cards[0]).filter(e=>e.tag==='button');
  assert.ok(firstButtons.some(b=>String(b.text).includes('Open board')),'Demo card opens the board with one click');
  console.log('Projects page: bundled demo boards listed first with Open actions');
})().catch(e=>{console.error(e);process.exitCode=1});
