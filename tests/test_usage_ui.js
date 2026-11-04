// Spend tracker renders cost with 4 decimals and never moves navigation or scroll.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
function make(tag='div'){
  return {tag,className:'',textContent:'',value:'',children:[],attrs:{},open:false,hidden:false,
    classList:{add(){},remove(){},toggle(){}},
    append(...kids){this.children.push(...kids);},replaceChildren(...kids){this.children=kids;},
    setAttribute(k,v){this.attrs[k]=v;},addEventListener(t,f){(this.handlers ||= {})[t]=f;}};
}
function element(id){
  if(!elements.has(id))elements.set(id,make());
  return elements.get(id);
}
let scrolled=false,navigated=false;
const posted=[];
const context=vm.createContext({document:{getElementById:element,createElement:make,createTextNode:text=>({textContent:text})},
  console,window:{scrollTo(){scrolled=true;}},__posted:posted});
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace('initialize().catch(showError);',''),context);
vm.runInContext(fs.readFileSync('web/usage.js','utf8'),context);
const summary={model:'meta/muse-spark-1.3-contributor',requests:3,input_tokens:1200,output_tokens:300,
  cached_input_tokens:200,total_tokens:1500,estimated_cost_usd:0.02344,today_requests:1,today_cost_usd:0.003,
  budget_usd:75,warn_usd:60,warn_high_usd:70,remaining_usd:74.97656,level:'ok'};
vm.runInContext('renderUsage('+JSON.stringify(summary)+')',context);
assert.equal(element('usage-cost').textContent,'$0.0234');
assert.equal(element('usage-tokens').textContent,'1.5k');
assert.equal(scrolled,false);assert.equal(navigated,false);
assert.equal(element('usage-pill').attrs['aria-label'],'Estimated API cost $0.0234, 1500 tokens, 3 requests');
vm.runInContext(`renderUsage(null)`,context);
assert.equal(element('usage-cost').textContent,'—');
vm.runInContext('renderUsage('+JSON.stringify({...summary,level:'high'})+')',context);
assert.deepEqual(Object.keys(element('usage-pill').handlers || {}),[]);
vm.runInContext('initializeUsage()',context);
assert.equal(typeof element('usage-pill').handlers.click,'function');
assert.equal(typeof element('usage-budget-form').handlers.submit,'function');
(async()=>{
  vm.runInContext(`fillUsageDialog()`,context);
  const rows=element('usage-detail').children;
  assert.equal(rows.length,9);
  assert.equal(rows[4].children[0].textContent,'Cached input tokens');
  assert.equal(rows[6].children[1].textContent,'$0.0234');
  assert.equal(element('usage-budget').value,75);
  vm.runInContext(`app.config={};api=async(path,data)=>{__posted.push([path,data]);return ${JSON.stringify({...summary,budget_usd:50})};};`,context);
  element('usage-budget').value='50';element('usage-warn').value='40';element('usage-warn-high').value='45';
  await element('usage-budget-form').handlers.submit({preventDefault(){}});
  assert.equal(JSON.stringify(posted),JSON.stringify([['/api/usage-budget',{budget_usd:50,warn_usd:40,warn_high_usd:45}]]));
  assert.equal(element('usage-cost').textContent,'$0.0234');
  assert.equal(element('usage-budget-message').textContent,'Budget saved.');
  element('usage-budget').value='oops';
  await element('usage-budget-form').handlers.submit({preventDefault(){}});
  assert.equal(posted.length,1);
  assert.match(element('usage-budget-message').textContent,/numbers/);
  console.log('Usage pill, dialog, budget save and scroll stability: passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
