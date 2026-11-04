// Exercise the actual settings continuation without a real API key or network.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('web/app.js','utf8').replace('initialize().catch(showError);','');
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{value:'',checked:false,textContent:'',classList:{remove(){},add(){}},close(){this.closed=true;}});return elements.get(id);}
const context=vm.createContext({document:{getElementById:element},console,AbortController,setTimeout,clearTimeout});
vm.runInContext(source,context);
vm.runInContext(fs.readFileSync('web/product-context.js','utf8'),context);
vm.runInContext(`
  function maybeReviewSavedProject(){return false;}
  function maybeShowReviewAttention(){}
  const realSetBusy=setBusy;
  settingsStatus=()=>{};setBusy=()=>{app.busy=app.pending;};renderStepChat=()=>{};
  api=async()=>({key_configured:true});
  app.resumeAfterKey=()=>{if(app.busy)throw new Error('Run remains disabled');globalResult='started';};
  globalResult='waiting';
`,context);
(async()=>{
 vm.runInContext(`
   originalApi=api;requests=[];app.config={csrf_token:'old'};
   fetch=async(path,options)=>{
     requests.push([path,options.headers?.['X-PCB-Token']]);
     if(path==='/api/config')return {ok:true,status:200,json:async()=>({csrf_token:'new'})};
     if(options.headers['X-PCB-Token']==='old')return {ok:false,status:403,json:async()=>({error:'Refresh this page before trying again.'})};
     return {ok:true,status:200,json:async()=>({done:true})};
   };
 `,context);
 // Retrieve the real API function from a fresh scope because the earlier setup mocks it.
 const apiSource=source.slice(source.indexOf('async function api('),source.indexOf('function settingsStatus('));
 vm.runInContext(apiSource,context);
 assert.equal((await vm.runInContext("api('/api/run',{})",context)).done,true);
 assert.equal(vm.runInContext('requests.length',context),3);
 vm.runInContext('api=async()=>({key_configured:true});',context);
 await vm.runInContext('saveSettings()',context);
 assert.equal(vm.runInContext('globalResult',context),'started');
 assert.equal(element('settings-dialog').closed,true);
 vm.runInContext(`api=async()=>{throw new Error('Invalid key');};app.resumeAfterKey=()=>{globalResult='started';};globalResult='waiting';`,context);
 await vm.runInContext('saveSettings()',context);
 assert.equal(vm.runInContext('globalResult',context),'waiting');
 assert.equal(element('settings-message').textContent,'Invalid key');
 vm.runInContext(`
   app.config={csrf_token:'previous-server',key_configured:true};app.revision=5;app.project=null;
   renderCount=0;statusChanged=false;projectRead=false;
   renderProject=()=>{renderCount++;};renderJob=()=>{};settingsStatus=()=>{statusChanged=true;};
   api=async(path)=>{
     if(path==='/api/state')return {revision:5,report_id:null,job:null};
     if(path==='/api/config')return {csrf_token:'new-server',key_configured:false};
     if(path==='/api/project'){projectRead=true;return null;}
   };
 `,context);
 vm.runInContext("showError(Object.assign(new Error('Temporarily offline'),{connectionError:true}));",context);
 await vm.runInContext('refresh()',context);
 assert.equal(element('error-banner').hidden,true);
 vm.runInContext("showError(new Error('A real form error'));",context);
 await vm.runInContext('refresh()',context);
 assert.equal(element('error-banner').hidden,false);
 assert.equal(vm.runInContext('app.config.key_configured',context),false);
 assert.equal(vm.runInContext('statusChanged && projectRead',context),true);
 assert.equal(vm.runInContext('renderCount',context),1);
 vm.runInContext(`
   workflow={revisionBase:null};designUI={chatBusy:false};
   analysisButton={disabled:true,dataset:{designAction:'analyze-pins'}};
   chatButton={disabled:true,dataset:{designAction:'chat'}};
   document.querySelectorAll=selector=>selector==='[data-design-action]'?[analysisButton,chatButton]:[];
   app.snapshot={report_id:'saved-review',job:null};app.pending=true;realSetBusy();
 `,context);
 assert.equal(vm.runInContext('analysisButton.disabled',context),true);
 assert.equal(vm.runInContext('chatButton.disabled',context),false,'Questions stay available during a board review');
 vm.runInContext('app.pending=false;realSetBusy();',context);
 assert.equal(vm.runInContext('analysisButton.disabled || chatButton.disabled',context),false);
 vm.runInContext("app.snapshot.job={status:'running'};realSetBusy();",context);
 assert.equal(vm.runInContext('analysisButton.disabled',context),true);
 assert.equal(vm.runInContext('chatButton.disabled',context),false);
 vm.runInContext('app.snapshot.job=null;',context);
 vm.runInContext('designUI.chatBusy=true;realSetBusy();',context);
 assert.equal(vm.runInContext('chatButton.disabled',context),true);
 assert.equal(vm.runInContext('analysisButton.disabled',context),false);
 vm.runInContext(`
   app.snapshot={revision:5,report_id:null,job:null};app.revision=5;
   app.config={csrf_token:'stable',key_configured:false};
   stateReads=0;releaseState=null;blockedState=new Promise(resolve=>releaseState=resolve);
   api=async(path)=>{
     if(path==='/api/state'){stateReads++;if(stateReads===1)await blockedState;return {revision:5,report_id:null,job:null};}
     if(path==='/api/config')return {csrf_token:'stable',key_configured:false};
     throw new Error('Unexpected request '+path);
   };
 `,context);
 const firstRefresh=vm.runInContext('refresh()',context);
 const secondRefresh=vm.runInContext('refresh()',context);
 assert.equal(vm.runInContext('stateReads',context),1);
 vm.runInContext('releaseState();',context);
 await Promise.all([firstRefresh,secondRefresh]);
 assert.equal(vm.runInContext('stateReads',context),2);
 vm.runInContext(`
   startupReads=0;startupWaits=0;
   setTimeout=callback=>{startupWaits++;callback();};
   api=async()=>{startupReads++;if(startupReads<3)throw Object.assign(new Error('Temporarily unavailable'),{connectionError:true});return {csrf_token:'restored'};};
 `,context);
 assert.equal((await vm.runInContext('startupConfig()',context)).csrf_token,'restored');
 assert.equal(vm.runInContext('startupReads',context),3);
 assert.equal(vm.runInContext('startupWaits',context),2);
 assert.equal(element('error-banner').hidden,true);
 vm.runInContext(`api=async()=>{throw new Error('Invalid configuration');};`,context);
 await assert.rejects(vm.runInContext('startupConfig()',context),/Invalid configuration/);
 console.log('Startup continuation: passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
