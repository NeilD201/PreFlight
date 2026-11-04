// Exercise duplicate writes and editable settings without a provider request.
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
  vm.runInContext(`
    app.config={csrf_token:'token',model:'gpt-6-luna',key_configured:false};
    calls=0;release=null;
    fetch=async()=>{calls++;await new Promise(resolve=>release=resolve);return {ok:true,json:async()=>({done:true})};};
  `,context);
  const first=vm.runInContext("api('/api/settings',{model:'gpt-6-luna'})",context);
  const second=vm.runInContext("api('/api/settings',{model:'gpt-6-luna'})",context);
  assert.equal(vm.runInContext('calls',context),1,'Double click must send one write');
  vm.runInContext('release()',context);await Promise.all([first,second]);
  assert.equal(vm.runInContext('app.inflightRequests.size',context),0,'Do not retain credentials in request cache');
  vm.runInContext("fetch=async()=>{throw Object.assign(new Error('timeout'),{name:'AbortError'});};",context);
  await assert.rejects(vm.runInContext("api('/api/file-fix/apply',{fix_id:'test'})",context),error=>{
    assert.equal(error.connectionError,false);assert.match(error.message,/may still be running/);return true;
  });
  await assert.rejects(vm.runInContext("api('/api/config')",context),error=>error.connectionError===true);
  vm.runInContext('openSettings()',context);
  element('openai-api-key').value='unsaved-test-value';
  element('ai-model').value='gemini-3.5-flash-lite';
  element('gemini-backup-enabled').checked=false;
  vm.runInContext('settingsStatus();openSettings();',context);
  assert.equal(element('openai-api-key').value,'unsaved-test-value');
  assert.equal(element('ai-model').value,'gemini-3.5-flash-lite');
  assert.equal(element('gemini-backup-enabled').checked,false);
  vm.runInContext('app.busy=true;calls=0;api=async()=>{calls++;};',context);
  await vm.runInContext('saveSettings(true)',context);
  assert.equal(vm.runInContext('calls',context),0,'A second submit must not start another connection test');
  console.log('Duplicate writes, timeout honesty, and settings draft preservation: passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
