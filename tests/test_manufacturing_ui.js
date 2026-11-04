// Older saved records may omit fields added by newer manufacturing checks.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
  constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.listeners={};this.dataset={};}
  append(...items){this.children.push(...items);}
  prepend(...items){this.children.unshift(...items);}
  replaceChildren(...items){this.children=items;}
  insertBefore(item,before){this.children.splice(this.children.indexOf(before),0,item);}
  contains(){return false;}
  setAttribute(){}
  addEventListener(type,fn){this.listeners[type]=fn;}
  click(){this.listeners.click?.();}
  querySelectorAll(){return [];}
}
function descendants(root){return [root,...root.children.flatMap(descendants)];}
const panel=new Element('section');let downloaded='';
const context=vm.createContext({
  console,setTimeout:()=>{},document:{createTextNode:text=>new Element('text',text)},
  node:(tag,_class,text='')=>new Element(tag,text),$:id=>id==='panel-manufacturing'?panel:null,
  app:{snapshot:{report_id:'test'},busy:false,project:{archive:'test',manufacturing:{
    selection:{manufacturer:'jlcpcb'},measurements:{status:'measured'},
    checks:[{id:'via_in_pad',status:'legacy',locations:[{}]}]}}},
  overviewUI:{section:'manufacturing'},renderOverview(){},
  Blob:class{constructor(parts){downloaded=parts.join('');}},
  URL:{createObjectURL:()=>'/mock',revokeObjectURL(){}},
});
vm.runInContext(fs.readFileSync('web/manufacturing.js','utf8'),context);
vm.runInContext('renderManufacturing()',context);
const all=descendants(panel);
assert.ok(all.some(item=>item.text==='Not verified'));
assert.doesNotMatch(all.map(item=>item.text).join('\n'),/undefined|NaN|\[object Object\]/);
all.find(item=>item.text==='Download order checklist').click();
assert.doesNotMatch(downloaded,/undefined|NaN|\[object Object\]/);
assert.match(downloaded,/Not recorded/);
console.log('Incomplete saved manufacturing records render and export without broken text: passed');
