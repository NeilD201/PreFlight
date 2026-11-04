// A delayed answer belongs to the requested issue even after navigation.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({console});
vm.runInContext(fs.readFileSync('web/design.js','utf8'),context);
vm.runInContext(`
  app={snapshot:{report_id:'report-a',job:{status:'running'}},config:{key_configured:true},busy:true,
    project:{provenance:{source_hashes:{'board.kicad_pcb':'hash-a'}},workspace:{design_context:{revision:0}},
      review:{groups:[{id:'R003',key:'k3',signature:'s3',title:'Antenna',summary:'Old RF evidence',evidence:['old']},{id:'R002',key:'k2',signature:'s2',title:'Other',summary:'Other',evidence:[]}]}}};
  $=()=>({value:'Explain this gap'});
  renderStepChat=()=>{designUI.chatMessages=designUI.chats.get(chatConversationKey(designUI.chatGroup,designUI.chatStep)) || [];};
  designUI.chatGroup='R003';designUI.chatStep=0;
  designUI.chats.set(chatConversationKey('R003',0),[]);designUI.chats.set(chatConversationKey('R002',0),[]);renderStepChat();
  resolveAnswer=null;api=()=>new Promise(resolve=>resolveAnswer=resolve);
`,context);
(async()=>{
  const answering=vm.runInContext('sendStepHelp({preventDefault(){}})',context);
  assert.equal(vm.runInContext('typeof resolveAnswer',context),'function','Chat must send while a review runs');
  assert.equal(vm.runInContext('designUI.chatBusy',context),true);
  // A second submit must not create another request while this answer is pending.
  await vm.runInContext('sendStepHelp({preventDefault(){}})',context);
  assert.equal(vm.runInContext("designUI.chats.get(chatConversationKey('R003',0)).length",context),1);
  vm.runInContext(`designUI.chatGroup='R002';renderStepChat();resolveAnswer({status:'completed',answer:'The gap alone does not prove a short.'});`,context);
  await answering;
  assert.equal(vm.runInContext("designUI.chats.get(chatConversationKey('R003',0))[1].text",context),'The gap alone does not prove a short.');
  assert.equal(vm.runInContext("designUI.chats.get(chatConversationKey('R002',0)).length",context),0);
  vm.runInContext(`designUI.chatGroup='R003';renderStepChat();`,context);
  const failedAnswer=vm.runInContext('sendStepHelp({preventDefault(){}})',context);
  vm.runInContext(`resolveAnswer({status:'failed',message:'Temporarily unavailable',detail:'HTTP 503'});`,context);
  await failedAnswer;
  assert.equal(vm.runInContext('designUI.chatMessages.at(-1).failed',context),true);
  assert.equal(vm.runInContext("designUI.chatDrafts.get('R003:0')",context),'Explain this gap');
  vm.runInContext(`api=(_url,body)=>{sentBody=body;return Promise.resolve({status:'ai',answer:'Recovered'});};`,context);
  await vm.runInContext('sendStepHelp({preventDefault(){}})',context);
  assert.equal(vm.runInContext("sentBody.history.some(m=>m.text.includes('Temporarily unavailable'))",context),false);
  vm.runInContext(`api=()=>new Promise(resolve=>resolveAnswer=resolve);`,context);
  const staleAnswer=vm.runInContext('sendStepHelp({preventDefault(){}})',context);
  vm.runInContext(`app.snapshot.report_id='report-b';designUI.chats.clear();resolveAnswer({status:'completed',answer:'Old report answer'});`,context);
  await staleAnswer;
  assert.equal(vm.runInContext('designUI.chats.size',context),0);
  // Reusing the same finding ID with new evidence must start a distinct conversation.
  const oldKey=vm.runInContext("chatConversationKey('R003',0)",context);
  vm.runInContext(`app.snapshot.report_id='report-a';app.project.provenance.source_hashes['board.kicad_pcb']='hash-b';`,context);
  const newKey=vm.runInContext("chatConversationKey('R003',0)",context);
  assert.notEqual(newKey,oldKey,'Changed source hashes must not reuse old replies');
  vm.runInContext(`designUI.chats.set(${JSON.stringify(newKey)},[]);app.project.review.groups[0].summary='New proof';`,context);
  assert.notEqual(vm.runInContext("chatConversationKey('R003',0)",context),newKey,'Changed finding evidence must not reuse old replies');
  vm.runInContext(`
    selectTab=()=>{};overviewUI={focus:'R003'};openOverviewIssue=()=>{};
    $=()=>({scrollIntoView(){}});calls=0;
    api=async()=>{calls++;return {status:'ai',answer:'A saved explanation'};};
  `,context);
  await vm.runInContext("openStepChat('R003',0,'Explain this step')",context);
  await vm.runInContext("openStepChat('R003',0,'Explain this step')",context);
  assert.equal(vm.runInContext('calls',context),1,'Reopening an explanation should reuse its answer');
  vm.runInContext("app.project.workspace.design_context.revision++;",context);
  await vm.runInContext("openStepChat('R003',0,'Explain this step')",context);
  assert.equal(vm.runInContext('calls',context),2,'Changed context must get a fresh explanation');
  console.log('Chat sends during review; duplicate submits and delayed replies handled: passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
