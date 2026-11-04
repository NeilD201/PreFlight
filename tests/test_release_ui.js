// Overview must show real saved geometry and preserve unresolved checks.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
  constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.listeners={};}
  append(...items){this.children.push(...items);}
  addEventListener(type,handler){this.listeners[type]=handler;}
}
function descendants(node){return [node,...node.children.flatMap(descendants)];}
const context=vm.createContext({
  console,node:(tag,_class,text='')=>new Element(tag,text),
  app:{snapshot:{report_id:'saved-board'}},
  selectTab:section=>{context.selected=section;},openOverviewIssue:id=>{context.opened=id;},
});
vm.runInContext(fs.readFileSync('web/release.js','utf8'),context);
context.project={components:{U1:{}},workspace:{board_visual:{status:'ready',views:{front:{file:'board-front.svg'}}}},
  review:{groups:[{id:'R1'}]},review_sections:{assignments:{R1:{attention:true,section:'manufacturing'}}},
  before_ordering:{checks:[{status:'missing'}]}};
let hero=vm.runInContext('releaseBoardOverview(project)',context);
let items=descendants(hero);
assert.equal(items.find(item=>item.tag==='img').src,'/reports/saved-board/board-front.svg');
assert.ok(items.some(item=>item.text==='1 finding in the saved design.'));
items.find(item=>item.tag==='button').listeners.click();
assert.equal(context.selected,'manufacturing');assert.equal(context.opened,'R1');
context.project.review_sections.assignments.R1.attention=false;
hero=vm.runInContext('releaseBoardOverview(project)',context);
assert.ok(descendants(hero).some(item=>item.text==='Analysis has remaining gaps.'),'No findings must not imply all checks passed');
context.project.workspace={};
hero=vm.runInContext('releaseBoardOverview(project)',context);
assert.equal(descendants(hero).filter(item=>item.tag==='img').length,0);
assert.ok(descendants(hero).some(item=>item.text.includes('create a board preview')));
console.log('Board overview: saved preview, correct next issue, and honest incomplete state passed');
// Overview actions select the correct issue without sending a request on open.
context.project.review_sections.assignments.R1.attention=true;
context.project.review.groups[0].title='Check battery connection';
context.project.action_plan={actions:[{group_id:'R1',title:'Check battery connection'}]};
context.issueTitle=(group,action)=>action?.title || group.title;
context.findingLabel=()=> 'Needs interpretation';
context.fileFixEntry=()=>new Element('button','Fix this issue');
context.openIssueChat=id=>{context.chatIssue=id;};
const queue=vm.runInContext('releaseIssueQueue(project)',context);
descendants(queue).find(item=>item.text==='Issue chat').listeners.click();
assert.equal(context.chatIssue,'R1');
assert.ok(descendants(queue).some(item=>item.text==='Fix this issue'));
context.app.project={design_context:{product_context:{experience:'Experienced EE — show electrical limits'}}};
// Checklist counts explain the missing work, without treating unknowns as faults.
context.rows=[{status:'checked'},{status:'checked'},{status:'partial',requirement:'review'},{status:'missing',requirement:'review'},{status:'missing',requirement:'limits'}];
assert.equal(vm.runInContext('releaseSectionSummary(rows)',context),'2 passed · 1 needs your input · 2 need more review');
assert.equal(vm.runInContext("releaseCheckState({status:'attention',requirement:'limits'}).label",context),'Needs a closer look');
assert.equal(vm.runInContext("releaseCheckState({status:'missing',requirement:'physical'}).label",context),'Needs a hands-on check');
assert.equal(vm.runInContext("releaseSectionSummary([{status:'checked'},{status:'checked'}])",context),'2 passed');
assert.equal(vm.runInContext("releaseSectionSummary([{status:'partial'}])",context),'1 needs more review');
