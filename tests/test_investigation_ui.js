const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.textContent='';}
  append(...children){this.children.push(...children);}
  get text(){return [this.textContent,...this.children.map(c=>c.text)].join(' ');}
}
const context=vm.createContext({document:{createElement:tag=>new Element(tag)}});
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace('initialize().catch(showError);',''),context);
context.box=new Element('div');
context.trace={product_hypothesis:'A sensor board',steps:[{title:'Retrieve evidence',status:'limited',detail:'Missing operating limits'}],
  tasks:[{references:['U1'],question:'Does SDA need a pull-up?',tool:'read_datasheet',reason:'Check the interface',
    status:'limited',limitations:['<script>throw Error("injection")</script>']}],limitations:[]};
vm.runInContext('renderInvestigation(box,trace)',context);
assert.match(context.box.text,/How Vector investigated/);
assert.match(context.box.text,/Missing operating limits/);
assert.match(context.box.text,/Evidence has limits/);
assert.match(context.box.text,/U1/);
assert.match(context.box.text,/<script>/); // Untrusted text is assigned to textContent.
assert.equal(context.box.children[0].tag,'details');
const count=context.box.children.length;
vm.runInContext('renderInvestigation(box,undefined)',context);
assert.equal(context.box.children.length,count,'Old reports remain renderable');
console.log('Investigation UI: evidence limits, expandable tools, literal text, and old reports passed');
