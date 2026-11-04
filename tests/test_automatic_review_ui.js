// Paid analysis must never auto-fire: it asks once per saved design/context with
// the cost stated, and runs only on an explicit press. Never on every poll.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('web/design.js','utf8'),storage=new Map();
function setup(){
  const app={view:'workspace',snapshot:{report_id:'r1'},config:{key_configured:true,model:'test'},project:{
    archive:'board',provenance:{source_hashes:{pcb:'abc'}},ai_review:{status:'stale'},
    workspace:{design_context:{revision:2},source_status:{status:'current'}}}};
  const stages=[],context=vm.createContext({app,document:{querySelector:()=>null},showError:e=>{throw e},
    sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)}});
  vm.runInContext(source,context);
  context.recordStage=(...args)=>stages.push(args);context.calls=0;
  vm.runInContext('analysisStage=recordStage;retryAIReview=async()=>{calls++;return true;}',context);
  return {app,stages,context,run:()=>vm.runInContext('maybeReviewSavedProject()',context)};
}
const a=setup();a.app.view='history';a.run();assert.equal(a.context.calls,0);
a.app.view='workspace';a.app.project.review_inputs={count:1};a.run();assert.equal(a.context.calls,0);
a.app.project.review_inputs={};a.app.project.workspace.source_status.status='changed';a.run();assert.equal(a.context.calls,0);
a.app.project.workspace.source_status.status='current';a.app.config.key_configured=false;a.run();assert.equal(a.context.calls,0);
a.app.config.key_configured=true;a.run();a.run();a.run();assert.equal(a.context.calls,0,'Paid runs need an explicit press');
assert.match(a.stages[0][1],/Run the Vector review/,'Consent dialog asks first');
assert.equal(a.stages[0][3],true,'Consent dialog offers explicit Run');
assert.equal(a.stages[0][4],'Run Vector review');
const reloaded=setup();reloaded.run();assert.equal(reloaded.context.calls,0,'Reload must not spend credits again');
a.app.snapshot.report_id='r2';a.run();assert.equal(a.context.calls,0,'New report ID alone must not restart analysis');
a.app.project.workspace.design_context.revision=3;a.run();assert.equal(a.context.calls,0,'New product answers ask again, never auto-fire');
a.app.project.ai_review.status='completed';a.run();assert.equal(a.context.calls,0);
a.app.project.ai_review.status='disabled';a.run();assert.equal(a.context.calls,0,'Respect disabled AI');
a.app.project.ai_review.status='failed';a.run();assert.equal(a.context.calls,0,'No automatic retry loop');
assert.equal(a.stages.at(-1)[3],true,'Failure has explicit retry');
a.app.snapshot.job={id:'job',status:'running',kind:'review',message:'Comparing pins'};a.run();
assert.equal(a.stages.at(-1)[2],'Comparing pins');
a.app.snapshot.job=null;a.app.project.ai_review.status='needs_context';assert.equal(a.run(),false,'Yield to the question dialog');
console.log('Automatic review: consent gate, reload deduplication, changed inputs, questions, failures and AI-off passed');
