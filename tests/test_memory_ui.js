const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];}append(...items){this.children.push(...items);}}
const ctx=vm.createContext({node:(tag,cls,text)=>new Element(tag,text)});
vm.runInContext(fs.readFileSync('web/release.js','utf8'),ctx);
ctx.review={devices:[{reference:'U42',kind:'DDR-like (generation unconfirmed)',identity:'Example memory',
  signals:[{name:'DQ0',number:'A1',net:'MEM_D0',peers:['U91']},{name:'CK_t',number:'B1',net:'',no_connect:true}]}],
  observations:[{detail:'U42 DQ0: no signal peer.'}],requirements:[{title:'Read/write timing at the chosen memory speed'}],
  limits:['No eye diagram or training verification.']};
const result=vm.runInContext('memoryReviewPanel(review)',ctx);
function all(n){return [n,...n.children.flatMap(all)];}
const text=all(result).map(n=>n.text || '').join('\n');
assert.match(text,/Memory interface review — not fully verified/);
assert.match(text,/DQ0 \(pin A1\) → MEM_D0 → U91/);
assert.match(text,/CK_t \(pin B1\) → marked unused/);
assert.match(text,/Still to verify/);
assert.match(text,/Check the selected bus mode/);
assert.doesNotMatch(text,/undefined|NaN|passed|Fix this issue/);
console.log('Memory view: pin facts, conditional observations and remaining checks passed');
