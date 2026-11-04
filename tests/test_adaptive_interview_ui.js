const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
  constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.listeners={};this.value='';}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  addEventListener(kind,fn){this.listeners[kind]=fn;}
  setAttribute(){} focus(){} showModal(){} remove(){}
  get lastChild(){return this.children.at(-1);}
  close(){this.listeners.close?.();}
  querySelectorAll(tag){return all(this).filter(n=>n.tag===tag);}
}
function all(node){return [node,...node.children.flatMap(all)];}
const body=new Element('body');let loads=0,savedAnswers={};
const parent={id:'radio',reference:'U1',question:'Use wireless?',known:'Radio fitted',hint:'Confirm use',choices:['yes','no'],choice_labels:['Yes','No']};
const child={id:'case',reference:'U1',question:'Is metal close to the antenna?',known:'Enclosure unknown',hint:'Confirm surroundings'};
const context=vm.createContext({app:{snapshot:{report_id:'other'}},node:(tag,cls,text='')=>new Element(tag,text),document:{body},showError:e=>{throw e},
  api:async(path,data)=>{
    assert.ok(['/api/product-questions','/api/product-answers'].includes(path));loads++;
    if(path==='/api/product-answers')Object.assign(savedAnswers,data.answers);
    const answers={...savedAnswers};
    const questions=answers.radio==='yes'?[parent,child]:[parent];
    questions.sort((a,b)=>Number(!!answers[a.id])-Number(!!answers[b.id]));
    return {answers:{...answers},questions,unanswered:questions.filter(q=>!answers[q.id]).map(q=>q.id),context_revision:1};
  }});
vm.runInContext(fs.readFileSync('web/product-context.js','utf8'),context);
(async()=>{
  const completed=vm.runInContext("interviewProduct({},'test','Save',false,'r',1)",context);
  await new Promise(r=>setImmediate(r));
  const dialog=body.children[0];
  all(dialog).find(e=>e.tag==='button' && e.text==='Yes').listeners.click();
  all(dialog).find(e=>e.tag==='form').listeners.submit({preventDefault(){}});
  await new Promise(r=>setImmediate(r));
  assert.ok(all(dialog).some(e=>e.text===child.question),'Follow-up inserted at index zero must not be skipped');
  const input=all(dialog).find(e=>e.id==='product-answer');input.value='Plastic enclosure';
  all(dialog).find(e=>e.tag==='form').listeners.submit({preventDefault(){}});
  await new Promise(r=>setImmediate(r));
  assert.ok(all(dialog).some(e=>e.text==='Here is what the review will use'));
  all(dialog).find(e=>e.tag==='button' && e.text==='Save').listeners.click();
  const answers=await completed;
  assert.equal(answers.radio,'yes');assert.equal(answers.case,'Plastic enclosure');
  assert.equal(loads,3);
  console.log('Adaptive interview: inserted follow-up answered before summary; no provider calls');
})().catch(e=>{console.error(e);process.exitCode=1});
