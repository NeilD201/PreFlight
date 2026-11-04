const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
class Element{
  constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.listeners={};this.value='';}
  set id(value){this._id=value;elements.set(value,this);} get id(){return this._id;}
  get lastChild(){return this.children.at(-1);}
  append(...items){this.children.push(...items);} replaceChildren(...items){this.children=items;}
  setAttribute(){} addEventListener(event,fn){this.listeners[event]=fn;} focus(){} showModal(){} close(){} remove(){this.removed=true;}
  querySelectorAll(tag){return all(this).filter(e=>e.tag===tag);}
}
function all(e){return[e,...e.children.flatMap(all)];}
const body=new Element('body');let revision=0,saved={},calls=[];
const questions=[{id:'purpose',reference:'Product',question:'What will it do?',known:'Saved board',hint:'Missing use'},
 {id:'environment',reference:'Product',question:'Where will it be used?',known:'Saved board',hint:'Missing environment'}];
const app={busy:false,snapshot:{report_id:'r1'},project:{workspace:{design_context:{revision:0,product_context:{}}}}};
const context=vm.createContext({console,document:{body},node:(tag,cls,text='')=>new Element(tag,text),app,
 showError:e=>{throw e;},api:async(path,data)=>{
  calls.push({path,data});
  if(path==='/api/product-answers'){assert.equal(data.context_revision,revision);saved={...saved,...data.answers};revision++;}
  const answers={...saved,...data.answers};
  const ordered=[...questions.filter(q=>!answers[q.id]),...questions.filter(q=>answers[q.id])];
  return {answers,questions:ordered,answered_questions:questions.filter(q=>answers[q.id]),
    unanswered:questions.filter(q=>!answers[q.id]).map(q=>q.id),context_revision:revision};
 }});
vm.runInContext(fs.readFileSync('web/product-context.js','utf8'),context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 app.project.saved_review_questions=['No further questions are needed.','What range is needed?'];
 const answerBox=new Element('div');context.answerBox=answerBox;
 vm.runInContext('renderQuestionAnswers(answerBox)',context);
 assert.equal(all(answerBox).filter(e=>e.tag==='textarea').length,1,'Acknowledgments are notes, not questions');
 assert(all(answerBox).some(e=>e.text==='No further questions are needed.'));
 delete app.project.saved_review_questions;
 const first=vm.runInContext("interviewProduct({},'project:r1','Review',false,'r1',0)",context);await flush();
 elements.get('product-answer').value='Battery logger';
 const dialog=body.children.at(-1);all(dialog).find(e=>e.tag==='form').listeners.submit({preventDefault(){}});await flush();
 assert.equal(saved.purpose,'Battery logger','Continue writes before finishing the interview');
 assert.equal(calls.filter(c=>c.path==='/api/product-answers').length,1);
 assert.equal(app.project.workspace.design_context.revision,1);
 all(dialog).find(e=>e.text==='×').listeners.click();assert.equal(await first,null);
 const second=vm.runInContext("interviewProduct(app.project.workspace.design_context.product_context,'project:r1','Review',false,'r1',1)",context);await flush();
 assert.equal(all(body.children.at(-1)).find(e=>e.tag==='label').text,'Where will it be used?','Reopening skips the saved question');
 all(body.children.at(-1)).find(e=>e.text==='Not sure yet').listeners.click();await flush();
 assert.equal(saved.environment,'Not sure yet');
 assert.ok(all(body.children.at(-1)).some(e=>e.text==='Close — answers saved'),'All answers lead to summary, not another question');
 all(body.children.at(-1)).find(e=>e.text==='Close — answers saved').listeners.click();await second;
 const third=vm.runInContext("interviewProduct(app.project.workspace.design_context.product_context,'project:r1','Review',false,'r1',2)",context);await flush();
 assert.equal(all(body.children.at(-1)).filter(e=>e.tag==='textarea').length,0,'Completed interview never restarts from question one');
 all(body.children.at(-1)).find(e=>e.text==='Close — answers saved').listeners.click();await third;
 console.log('Answer persistence: Continue saves, close retains, reopen skips, unknown stays answered, completed summary passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
