const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
 constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.listeners={};this.value='';}
 append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}
 setAttribute(){}addEventListener(name,fn){this.listeners[name]=fn;}showModal(){this.open=true;}close(){this.open=false;this.listeners.close?.();}remove(){}focus(){}
 querySelectorAll(selector){const tags=selector.split(',');return this.children.flatMap(walk).filter(e=>tags.includes(e.tag));}
}
function walk(e){return[e,...e.children.flatMap(walk)];}
const body=new Element('body'),scheduled=[];
const task={id:'physical:mechanical',signature:'v1',kind:'physical',question:'Does it fit the case?',hint:'Describe what you checked.'};
const context=vm.createContext({console,document:{body,querySelector:()=>null},setTimeout:fn=>scheduled.push(fn),node:(tag,cls,text='')=>new Element(tag,text),showError:()=>{},
 app:{busy:false,adding:false,view:'workspace',config:{key_configured:true},snapshot:{report_id:'r1'},project:{ai_review:{status:'completed'},review_inputs:{tasks:[task],count:1},workspace:{design_context:{revision:0}}}}});
vm.runInContext(fs.readFileSync('web/product-context.js','utf8'),context);
const find=text=>walk(body).find(e=>e.text===text);
(async()=>{
 vm.runInContext('calls=[];api=(path,data)=>{calls.push({path,data});return new Promise(resolve=>resolveSave=resolve);};refresh=async()=>{app.project.review_inputs={tasks:[],count:0};app.project.workspace.design_context.revision=1;};',context);
 const done=vm.runInContext('interviewReviewInputs()',context);
 assert.equal(body.children[0].open,true,'Missing inputs open a modal');
 const unknown=find('I don’t know yet');const save=unknown.listeners.click();unknown.listeners.click();
 assert.equal(vm.runInContext('calls.length',context),1,'Double click saves once');
 assert.equal(vm.runInContext('calls[0].data.review_task.signature',context),'v1');
 find('Pause for now').listeners.click();assert.equal(body.children[0].open,true,'Cannot close during an active save');
 vm.runInContext('resolveSave({revision:1})',context);await save;
 assert.ok(find('Continue review'),'Save advances without finding another page');
 find('Continue review').listeners.click();assert.equal(await done,true);
 // Questions from a completed AI review also pop up, once per report.
 vm.runInContext("app.project.review_inputs={tasks:[{question:'New question?'}],count:1};editReviewInputs=async()=>{opened=(typeof opened==='undefined'?0:opened)+1;};maybeAskProductQuestions();maybeAskProductQuestions();",context);
 assert.equal(scheduled.length,1);await scheduled.shift()();assert.equal(vm.runInContext('opened',context),1);
 vm.runInContext("app.snapshot.report_id='r2';maybeAskProductQuestions();app.snapshot.report_id='r3';",context);
 await scheduled.shift()();assert.equal(vm.runInContext('opened',context),1,'Do not open questions for a different report');
 console.log('Review questions: modal, durable save request, duplicate guard, safe pause, next step and once-per-report auto-open passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
