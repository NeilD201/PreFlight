// Findings get one prominent prompt, not a new popup on each polling refresh.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
  constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.listeners={};}
  append(...items){this.children.push(...items);}
  setAttribute(){}
  addEventListener(type,handler){this.listeners[type]=handler;}
  showModal(){this.open=true;}
  close(){this.open=false;this.listeners.close?.();}
  remove(){this.removed=true;}
}
const body=new Element('body'),workspace={hidden:false};
const app={snapshot:{report_id:'board-a'},project:{archive:'Board A',review:{groups:[{id:'R1',title:'Antenna missing'},{id:'R2',title:'Library note'}]},review_sections:{assignments:{R1:{attention:true,section:'traces'},R2:{attention:false,section:'notes'}}}}};
const context=vm.createContext({app,node:(tag,cls,text='')=>new Element(tag,text),$:()=>workspace,
 navigate:view=>{app.view=view;},
 document:{body,querySelector:()=>body.children.find(el=>el.open)},issueTitle:g=>g.title,findingLabel:()=> 'Finding',
 selectTab:section=>context.section=section,openOverviewIssue:id=>context.issue=id});
vm.runInContext(fs.readFileSync('web/release.js','utf8'),context);
const run=()=>vm.runInContext('maybeShowReviewAttention()',context);
const all=n=>[n,...n.children.flatMap(all)];
app.busy=true;run();assert.equal(body.children.length,0);
app.busy=false;run();assert.equal(body.children.length,0,'Startup must finish navigation before prompting');
app.view='workspace';workspace.hidden=true;run();assert.equal(body.children.length,0);
workspace.hidden=false;run();assert.equal(body.children.length,0,'Do not hand unanalysed findings to the user');
app.project.ai_review={status:'completed'};app.project.review_inputs={count:1};run();assert.equal(body.children.length,0,'Ask the missing product question before presenting results');
app.project.review_inputs={count:0};run();assert.equal(body.children.length,1);
const first=body.children[0];
assert.ok(all(first).some(el=>el.text==='Your analysis results'));
assert.ok(!all(first).some(el=>el.text==='Library note'));
run();assert.equal(body.children.length,1);
all(first).find(el=>el.text==='Not now').listeners.click();run();assert.equal(body.children.length,1);
app.snapshot.report_id='board-b';run();assert.equal(body.children.length,2);
all(body.children[1]).find(el=>el.text==='See results').listeners.click();
assert.equal(context.section,'traces');assert.equal(context.issue,'R1');run();assert.equal(body.children.length,2);
app.project.review_sections.assignments.R2.attention=true;run();assert.equal(body.children.length,3);
assert.ok(all(body.children[2]).some(el=>el.text==='Library note'));
app.project.review_sections.assignments.R1.attention=false;app.project.review_sections.assignments.R2.attention=false;
run();assert.equal(body.children[2].open,false);assert.equal(body.children.length,3);
console.log('Review prompt: visible findings, navigation, dismissal, new results, and polling passed');
