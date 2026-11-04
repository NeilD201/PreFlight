const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
class Element{
 constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.listeners={};this.value='';}
 set id(v){this._id=v;elements.set(v,this);}get id(){return this._id;}
 append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}
 setAttribute(){}addEventListener(e,f){this.listeners[e]=f;}focus(){}
}
const home=new Element('section');elements.set('chat-home',home);
const context=vm.createContext({console,node:(tag,cls,text='')=>new Element(tag,text),$:id=>elements.get(id),
 app:{snapshot:{report_id:null},project:null,config:{key_configured:true,model:'gpt-6-luna'}},navigate:()=>{},openSettings:()=>{}});
vm.runInContext(fs.readFileSync('web/design.js','utf8'),context);
function all(e){return[e,...e.children.flatMap(all)];}
(async()=>{
 vm.runInContext('calls=[];api=(path,data)=>{calls.push({path,data});return new Promise(r=>resolveReply=r);};openChatHome();',context);
 assert.ok(elements.has('general-chat-question'));assert.equal(all(home).some(e=>e.tag==='select'),false);
 assert.equal(vm.runInContext('calls.length',context),0);
 const first=vm.runInContext("sendGeneralChat('Hello')",context);
 await vm.runInContext("sendGeneralChat('Hello')",context);
 assert.equal(vm.runInContext('calls.length',context),1,'Duplicate submits must not spend twice');
 assert.equal(vm.runInContext('calls[0].data.web_search',context),false,'Web search stays off unless the checkbox is ticked');
 vm.runInContext("resolveReply({status:'ai',answer:'Hi!',evidence_state:{status:'changed',warning:'Your KiCad files changed. Update review.'}});",context);await first;
 assert.ok(all(home).some(e=>e.text==='Your KiCad files changed. Update review.'),'Stale evidence warning must be visible with the answer');
 assert.equal(vm.runInContext('generalConversation().messages.length',context),2);
 vm.runInContext("$('chat-web-search').checked=true",context);
 const toggled=vm.runInContext("sendGeneralChat('Search the web for this part')",context);
 assert.equal(vm.runInContext('calls[1].data.web_search',context),true,'Ticked checkbox sends web_search through Meta grounding');
 vm.runInContext("resolveReply({status:'ai',answer:'Grounded.',web_searched:true});",context);await toggled;
 assert.ok(all(home).some(e=>e.text==='Vector · Web searched'),'Grounded answers are labelled as web searched');
 vm.runInContext('generalConversation().webSearch=false',context);
 const second=vm.runInContext("sendGeneralChat('What can you help with?')",context);
 assert.equal(vm.runInContext('calls[2].data.history[1].text',context),'Hi!');
 assert.equal(vm.runInContext('calls[2].data.web_search',context),false);
 vm.runInContext("resolveReply({status:'failed',message:'Offline'});",context);await second;
 const failure=vm.runInContext('generalConversation().messages.at(-1)',context);
 context.failure=failure;
 const retry=vm.runInContext('sendGeneralChat(failure.question,failure)',context);
 assert.deepEqual(vm.runInContext('calls[3].data.history',context),vm.runInContext('failure.history',context),'Retry uses the original history');
 vm.runInContext("resolveReply({status:'ai',answer:'I can help.'});",context);await retry;
 vm.runInContext("app.snapshot.report_id='different';openChatHome();",context);
 assert.equal(vm.runInContext('generalConversation().messages.length',context),0,'New project does not inherit other conversations');
 all(home).find(e=>e.text==='Clear chat view').listeners.click();
 assert.equal(vm.runInContext('calls.length',context),4,'New chat does not call AI');
 vm.runInContext(`app.project={workspace:{project_key:'stable-project',chat_history:[{question:'Use a plastic case',answer:'Recorded',status:'ai'}]}};openChatHome();`,context);
 assert.equal(vm.runInContext('generalConversation().messages[0].text',context),'Use a plastic case','Saved conversation restored after reload');
 vm.runInContext(`app.snapshot.report_id='new-revision';app.project.workspace.design_context={revision:7};openChatHome();`,context);
 assert.equal(vm.runInContext('generalConversation().messages.length',context),2,'Project chat survives a new review and context revision');
 console.log('General chat: no issue selectors, no-project use, followups, duplicate guard, retry and isolation passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
// Provider citations become safe inline links; raw HTML remains plain text.
context.cited={text:'A cited fact [1].',citations:[{start:13,end:16,url:'https://example.com/doc',title:'Manufacturer'}]};
const paragraph=vm.runInContext('chatAnswerContent(cited)',context);
assert.equal(paragraph.children.find(e=>e.tag==='a').href,'https://example.com/doc');
context.cited.citations[0].url='javascript:alert(1)';
assert.equal(vm.runInContext('chatAnswerContent(cited)',context).children.some(e=>e.tag==='a'),false);
