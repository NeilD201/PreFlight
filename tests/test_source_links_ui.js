const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
  constructor(tag,text=''){this.tag=tag;this.textContent=text||'';this.children=[];}
  append(...items){this.children.push(...items);}
}
function all(e){return[e,...e.children.flatMap(all)];}
function texts(e){return all(e).map(n=>n.textContent||n.text||'').join(' ');}
const context=vm.createContext({console,URL,
  document:{createElement:tag=>new Element(tag)},
  node:(tag,cls,text='')=>new Element(tag,text)});
context.Element=Element;
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace('initialize().catch(showError);',''),context);
vm.runInContext(`function renderSources(sources){const parent=new Element('div');sourceLinks(parent,sources);return parent;}`,context);
(async()=>{
  const renderSources=vm.runInContext('renderSources',context);
  const noUrl=renderSources([{title:'NE555 datasheet, page 9'}]);
  assert.ok(texts(noUrl).includes('NE555 datasheet, page 9'),'Title-only datasheet citation renders instead of vanishing');
  const linked=renderSources([{title:'LCSC part',url:'https://www.lcsc.com/part'}]);
  assert.ok(all(linked).some(e=>e.tag==='a'&&(e.textContent||e.text)==='LCSC part'),'https sources still render as links');
  const evil=renderSources([{title:'x',url:'javascript:alert(1)'}]);
  assert.ok(!all(evil).some(e=>e.tag==='a'),'Non-http protocols never become links');
  assert.ok(texts(evil).includes('x'),'Unsafe URL falls back to plain text');
  assert.equal(renderSources([]).children.length,0,'Empty sources render nothing');
  assert.equal(renderSources([{}]).children.length,0,'Title-less sources are skipped');
  console.log('Sources: title-only datasheet citations render; links and unsafe URLs handled passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
