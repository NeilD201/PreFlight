const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
  constructor(text){this.textContent=text;this.dataset={};this.listeners={};}
  addEventListener(name,fn){this.listeners[name]=fn;}
}
let calls=[],release,settings=0,updates=0;
const context=vm.createContext({app:{busy:false,pending:false,config:{key_configured:true},snapshot:{report_id:'current'},project:{ai_review:{status:'failed'},workspace:{source_status:{status:'current'}}}},
  node:(tag,cls,text)=>new Element(text),clearError(){},setBusy(){},showError(e){throw e;},
  api:(path,data)=>{calls.push({path,data});return new Promise(resolve=>{release=resolve});},refresh:async()=>{},
  openSettings(){settings++;},updateSavedProject(){updates++;}});
vm.runInContext(fs.readFileSync('web/design.js','utf8'),context);
(async()=>{
  const retry=vm.runInContext('aiRetryButton()',context);
  assert.equal(retry.textContent,'Retry Vector review');
  const pending=retry.listeners.click();await retry.listeners.click();
  assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/review-ai');assert.equal(calls[0].data.report_id,'current');
  assert.deepEqual(Object.keys(calls[0].data),['report_id','context_intake','connections_only'],'Retry must not overwrite context or request datasheet scraping');
  assert.equal(calls[0].data.context_intake,true);assert.equal(calls[0].data.connections_only,false);
  release({});await pending;assert.equal(context.app.pending,false);
  context.app.project.ai_review.status='completed';assert.equal(vm.runInContext('aiRetryButton()',context).textContent,'Review again');
  context.app.project.ai_review.status='stale';assert.equal(vm.runInContext('aiRetryButton()',context).textContent,'Run Vector review');
  context.app.config.key_configured=false;await vm.runInContext('retryAIReview()',context);assert.equal(settings,1);assert.equal(calls.length,1);
  context.app.project.workspace.source_status.status='changed';
  const changed=vm.runInContext('aiRetryButton()',context);assert.equal(changed.textContent,'Update changed files');
  changed.listeners.click();assert.equal(updates,1);assert.equal(calls.length,1);
  console.log('AI retry: one request, current revision, preserved context, settings and changed-file routing passed');
})().catch(error=>{console.error(error);process.exitCode=1});
