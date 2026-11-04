const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
class Element{
  constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.listeners={};this.value='';this.rows=0;this.maxLength=0;}
  set id(value){this._id=value;elements.set(value,this);} get id(){return this._id;}
  get lastChild(){return this.children.at(-1);}
  append(...items){this.children.push(...items);} replaceChildren(...items){this.children=items;}
  setAttribute(){} addEventListener(event,fn){this.listeners[event]=fn;} focus(){} showModal(){} close(){} remove(){this.removed=true;}
}
function all(e){return[e,...e.children.flatMap(all)];}
const body=new Element('body');
let revision=0,description='',calls=[];
const app={busy:false,snapshot:{report_id:'r1'},project:{workspace:{design_context:{revision:0,product_context:{}}}}};
const errors=[];
const context=vm.createContext({console,document:{body},node:(tag,cls,text='')=>new Element(tag,text),app,
 showError:e=>errors.push(e.message),refresh:async()=>{},api:async(path,data)=>{
  calls.push({path,data});
  if(path==='/api/project-context'){
    if(data.description!==undefined){
      assert.equal(data.context_revision,revision,'Description save carries the current revision');
      description=data.description;revision++;
    }
    return {description,context_revision:revision};
  }
  return {answers:{},questions:[],answered_questions:[],unanswered:[],context_revision:revision};
 }});
vm.runInContext(fs.readFileSync('web/product-context.js','utf8'),context);
const drain=async()=>{for(let i=0;i<10;i++)await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
  const first=vm.runInContext("interviewProduct({},'report:r1','Use answers & review',false,'r1',0,'check')",context);
  await drain();
  const dialog=body.children.at(-1);
  assert.ok(all(dialog).some(e=>e.tag==='h2'&&e.text==='Tell PreFlight about your project'),'Description screen leads the interview');
  const area=all(dialog).find(e=>e.tag==='textarea');
  assert.equal(area.rows,10,'Large multiline box');
  assert.equal(area.maxLength,6000,'Long descriptions allowed');
  area.value='Battery-powered ESP32 sensor node, BME280 over I2C at 3.3 V.';
  all(dialog).find(e=>e.text==='Save & analyse').listeners.click();
  await drain();
  assert.equal(description,'Battery-powered ESP32 sensor node, BME280 over I2C at 3.3 V.','Save stores the description');
  assert.equal(app.project.workspace.design_context.description,description,'Local context updated for the pipeline');
  assert.equal(app.project.workspace.design_context.revision,1);
  assert.ok(!calls.some(c=>c.data&&c.data.ai===true),'Saving the description never requests AI');
  assert.ok(all(body.children.at(-1)).some(e=>typeof e.text==='string'&&e.text.includes('No unanswered questions')),'Saved description continues the normal flow');
  all(body.children.at(-1)).find(e=>e.text==='Continue review').listeners.click();
  assert.equal(JSON.stringify(await first),'{}','Finish returns the answers for local reconsideration');
  const second=vm.runInContext("interviewProduct({},'report:r1','Use answers & review',false,'r1',1,'check')",context);
  await drain();
  const area2=all(body.children.at(-1)).find(e=>e.tag==='textarea');
  assert.equal(area2.value,description,'Reopening preserves the description for editing');
  all(body.children.at(-1)).find(e=>e.text==='×').listeners.click();
  assert.equal(await second,null);
  assert.deepEqual(errors,[],'No errors shown');
  console.log('Project description: one free-text box, saved revision, no AI spend, editable on reopen passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
