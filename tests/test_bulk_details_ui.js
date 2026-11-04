const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
  constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.listeners={};this.value='';this.disabled=false;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  addEventListener(kind,fn){this.listeners[kind]=fn;}
  setAttribute(){} focus(){} showModal(){} remove(){}
  get lastChild(){return this.children.at(-1);}
  close(){this.listeners.close?.();}
  querySelectorAll(tag){return all(this).filter(n=>n.tag===tag);}
}
function all(node){return [node,...node.children.flatMap(all)];}
const body=new Element('body');let savedAnswers={};
const parent={id:'radio',reference:'U1',question:'Use wireless?',known:'Radio fitted',hint:'Confirm use'};
const child={id:'case',reference:'U1',question:'Is metal close to the antenna?',known:'Enclosure unknown',hint:'Confirm surroundings'};
const context=vm.createContext({app:{snapshot:{report_id:'r'}},node:(tag,cls,text='')=>new Element(tag,text),document:{body},showError:e=>{throw e},
  api:async(path,data)=>{
    assert.ok(['/api/product-questions','/api/product-answers'].includes(path));
    if(path==='/api/product-answers')Object.assign(savedAnswers,data.answers);
    const answers={...savedAnswers};
    const questions=[parent,child];
    return {answers:{...answers},questions,unanswered:questions.filter(q=>!answers[q.id]).map(q=>q.id),context_revision:1};
  }});
vm.runInContext(fs.readFileSync('web/product-context.js','utf8'),context);
(async()=>{
  const completed=vm.runInContext("interviewProduct({},'test','Save',false,'x',1)",context);
  await new Promise(r=>setImmediate(r));
  const dialog=body.children[0];
  assert.ok(all(dialog).some(e=>e.text==='A detail is needed to continue'));
  all(dialog).find(e=>e.tag==='button' && e.text==='Enter all details').listeners.click();
  await new Promise(r=>setImmediate(r));
  const areas=all(dialog).filter(e=>e.tag==='textarea' && String(e.id||'').startsWith('bulk-'));
  assert.equal(areas.length,1,'Bulk page must show one paste box for all open questions');
  assert.equal(areas[0].id,'bulk-all','Single paste box');
  areas[0].value='yes\n\nPlastic enclosure\n\nSpare detail: USB-C cable';
  all(dialog).find(e=>e.tag==='button' && e.text==='Save all details').listeners.click();
  await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
  assert.ok(all(dialog).some(e=>e.text==='Here is what the review will use'));
  all(dialog).find(e=>e.tag==='button' && e.text==='Save').listeners.click();
  const answers=await completed;
  assert.equal(answers.radio,'yes','First paragraph answers the first question');
  assert.equal(answers.case,'Plastic enclosure\n\nSpare detail: USB-C cable','Extra paragraphs append to the last answer, never dropped');
  console.log('Bulk details: one paste box answers every open question; no provider calls');
})().catch(e=>{console.error(e);process.exitCode=1});

(async()=>{
  const body2=new Element('body');let saved2={};
  const context2=vm.createContext({app:{snapshot:{report_id:'r'}},node:(tag,cls,text='')=>new Element(tag,text),document:{body:body2},showError:e=>{throw e},
    api:async(path,data)=>{
      if(path==='/api/product-answers')Object.assign(saved2,data.answers);
      const answers={...saved2};
      const questions=[parent,child];
      return {answers,questions,unanswered:questions.filter(q=>!answers[q.id]).map(q=>q.id),context_revision:1};
    }});
  vm.runInContext(fs.readFileSync('web/product-context.js','utf8'),context2);
  const completed=vm.runInContext("interviewProduct({},'test','Save',false,'x',1)",context2);
  await new Promise(r=>setImmediate(r));
  const dialog=body2.children[0];
  all(dialog).find(e=>e.tag==='button' && e.text==='Skip all').listeners.click();
  await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
  assert.ok(all(dialog).some(e=>e.text==='Here is what the review will use'),'Skip all reaches the summary');
  assert.equal(saved2.radio,'Not sure yet','Skipped questions save as undecided, not invented');
  assert.equal(saved2.case,'Not sure yet','All remaining questions are skipped at once');
  all(dialog).find(e=>e.tag==='button' && e.text==='Save').listeners.click();
  await completed;
  console.log('Bulk details: skip-all leaves every open detail undecided; no provider calls');
})().catch(e=>{console.error(e);process.exitCode=1});
