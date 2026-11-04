// Top-level review sections share a panel but retain their own selection and focus.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
function element(id){
  if(!elements.has(id))elements.set(id,{id,attrs:{},hidden:false,value:'',setAttribute(key,value){this.attrs[key]=value;}});
  return elements.get(id);
}
const names=['connections','board','manufacturing','assembly','project','ai','chat'];
const buttons=names.map(name=>Object.assign(element('tab-'+name),{dataset:{tab:name}}));
const panels=['overview','manufacturing','assembly','project','ai','chat'].map(name=>element('panel-'+name));
const context=vm.createContext({console,document:{getElementById:element,querySelectorAll(selector){return selector==='[data-tab]'?buttons:panels;}}});
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace('initialize().catch(showError);',''),context);
vm.runInContext(fs.readFileSync('web/overview.js','utf8'),context);
vm.runInContext(`
  app.project={review:{groups:[{id:'R1',priority:'review',components:[]}]},review_sections:{assignments:{R1:{section:'placement'}}}};
  renderSelectedDetails=()=>{};renderOverviewIssues=()=>{};
  renderOverview=()=>syncReviewNavigation();
  selectTab('placement');
`,context);
assert.equal(element('panel-overview').hidden,false);
assert.equal(element('panel-project').hidden,true);
assert.equal(element('tab-board').attrs['aria-selected'],'true');
assert.equal(element('panel-overview').attrs['aria-labelledby'],'tab-board');
assert.equal(element('tab-board').tabIndex,0);
vm.runInContext("selectTab('ai')",context);
assert.equal(element('panel-ai').hidden,false);
assert.equal(element('panel-overview').hidden,true);
assert.equal(element('more-view').value,'ai');
vm.runInContext("selectTab('connections')",context);
assert.equal(element('more-view').value,'');
assert.equal(element('tab-connections').attrs['aria-selected'],'true');
vm.runInContext("openOverviewIssue('R1')",context);
assert.equal(element('tab-board').attrs['aria-selected'],'true');
assert.equal(element('tab-connections').attrs['aria-selected'],'false');
vm.runInContext("selectTab('notes')",context);
assert.equal(element('more-view').value,'notes');
assert.equal(element('panel-overview').attrs['aria-labelledby'],'more-view');
vm.runInContext("selectTab('project')",context);
assert.equal(element('panel-project').hidden,false);
assert.equal(element('panel-overview').hidden,true);
vm.runInContext("selectTab('manufacturing')",context);
assert.equal(element('panel-manufacturing').hidden,false);
assert.equal(element('tab-manufacturing').attrs['aria-selected'],'true');
assert.equal(element('more-view').value,'');
console.log('Top menus, shared review panel, notes and cross-section focus: passed');
vm.runInContext(`
 app.project.review_sections.assignments.R1.section='manufacturing';
 selectTab('connections');openOverviewIssue('R1');
`,context);
assert.equal(element('panel-manufacturing').hidden,false);
assert.equal(element('tab-manufacturing').attrs['aria-selected'],'true');
vm.runInContext("chooseReviewSection('notes')",context);
assert.equal(element('panel-overview').hidden,false);
assert.equal(element('panel-manufacturing').hidden,true);
assert.equal(element('more-view').value,'notes');

vm.runInContext("renderChatHome=()=>{};selectTab('chat')",context);
assert.equal(element('panel-chat').hidden,false);
assert.equal(element('panel-overview').hidden,true);
assert.equal(element('more-view').value,'');
vm.runInContext("selectTab('release')",context);
assert.equal(element('panel-chat').hidden,true);
