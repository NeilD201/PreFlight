// A page that already reached the server must not claim it was never started.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
function element(id){
  if(!elements.has(id))elements.set(id,{value:'',checked:false,textContent:'',open:false,
    classList:{remove(){},add(){}},showModal(){this.open=true;},close(){this.open=false;}});
  return elements.get(id);
}
const context=vm.createContext({document:{getElementById:element},console,AbortController,setTimeout,clearTimeout});
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace('initialize().catch(showError);',''),context);
(async()=>{
  vm.runInContext(`app.config={csrf_token:'token'};`,context);
  vm.runInContext(`fetch=async()=>{throw new Error('connection refused');};`,context);
  await assert.rejects(vm.runInContext("api('/api/config')",context),/Cannot reach the local app/);
  vm.runInContext(`fetch=async()=>({ok:true,status:200,json:async()=>({csrf_token:'token'})});`,context);
  await vm.runInContext("api('/api/config')",context);
  vm.runInContext(`fetch=async()=>{throw new Error('connection reset');};`,context);
  await assert.rejects(vm.runInContext("api('/api/config')",context),error=>{
    assert.match(error.message,/stopped responding/);
    assert.equal(error.connectionError,true);return true;
  });
  console.log('Reconnect honesty: never-contacted guidance and lost-contact reporting passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
