const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this._text='';this.listeners={};}
  get textContent(){return this._text;}
  set textContent(v){this._text=String(v);if(v==='')this.children=[];}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(k,v){this[k]=v;}
  click(){this.clicked=true;}
  addEventListener(type,fn){(this.listeners[type]=this.listeners[type]||[]).push(fn);}
  get text(){return [this.textContent,...this.children.map(c=>c.text)].join(' ');}
  find(tag){return this.children.flatMap(c=>[c,...c.find(tag)]).filter(e=>e.tag===tag);}
}
const registry={};
for(const id of ['staged-list','upload-checklist','commit-staged','upload-label','project-name','project-files-status'])registry[id]=new Element('div');
const context=vm.createContext({document:{createElement:tag=>new Element(tag),getElementById:id=>registry[id]},
  window:{},app:{upload:null,uploadFiles:null,busy:false}});
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace('initialize().catch(showError);',''),context);
vm.runInContext(`assert=undefined`,context);

// Pure helpers.
assert.equal(vm.runInContext(`suffixOf('board.KICAD_PCB')`,context),'.kicad_pcb');
assert.equal(vm.runInContext(`formatBytes(1536)`,context),'1.5 KB');
assert.equal(vm.runInContext(`typeOfFile('x.kicad_sch')`,context),'Schematic');
assert.equal(vm.runInContext(`checklistState(['a.kicad_pro','b.kicad_sch']).waitingPcb`,context),true);
assert.equal(vm.runInContext(`checklistState(['a.kicad_pro','b.kicad_sch']).ready`,context),false);
assert.equal(vm.runInContext(`checklistState(['a.kicad_pro','b.kicad_sch','c.kicad_pcb']).ready`,context),true);
assert.equal(vm.runInContext(`validateStaged(['a.kicad_pro','b.kicad_sch']).ok`,context),false);
assert.match(vm.runInContext(`validateStaged(['a.kicad_pro','b.kicad_sch']).error`,context),/project, schematic, and PCB/);
assert.equal(vm.runInContext(`validateStaged(['a.kicad_pro','b.kicad_sch','c.kicad_pcb']).ok`,context),true);
assert.match(vm.runInContext(`validateStaged(['a.kicad_pro','b.kicad_sch','c.csv']).error`,context),/cannot be uploaded directly/);
assert.match(vm.runInContext(`validateStaged(['a.kicad_pro','b.kicad_sch','d.kicad_dru']).error`,context),/Missing/);

// Staged rendering: files, types, sizes, checklist, waiting message, remove buttons.
vm.runInContext(`stagedFiles.set('board.kicad_pro',{file:{name:'board.kicad_pro',size:2048},name:'board.kicad_pro',size:2048,status:'staged',committable:true,note:''});
stagedFiles.set('board.kicad_sch',{file:{name:'board.kicad_sch',size:15360},name:'board.kicad_sch',size:15360,status:'staged',committable:true,note:''});
renderUploader()`,context);
assert.match(registry['staged-list'].text,/board\.kicad_pro/);
assert.match(registry['staged-list'].text,/Project · 2\.0 KB/);
assert.match(registry['staged-list'].text,/Schematic · 15\.0 KB/);
assert.match(registry['staged-list'].text,/Staged/);
assert.equal(registry['staged-list'].find('button').length,2);
assert.match(registry['upload-checklist'].text,/✓ Project/);
assert.match(registry['upload-checklist'].text,/○ PCB/);
assert.match(registry['upload-checklist'].text,/Waiting for PCB file \(\.kicad_pcb\)\./);
assert.equal(registry['commit-staged'].hidden,false);
assert.equal(registry['commit-staged'].disabled,true);

// Complete set enables commit; CSV is listed but skipped.
vm.runInContext(`stagedFiles.set('board.kicad_pcb',{file:{name:'board.kicad_pcb',size:4096},name:'board.kicad_pcb',size:4096,status:'staged',committable:true,note:''});
stagedFiles.set('bom.csv',{file:{name:'bom.csv',size:512},name:'bom.csv',size:512,status:'skipped',committable:false,note:'add through a folder or ZIP'});
renderUploader()`,context);
assert.equal(registry['commit-staged'].disabled,false);
assert.match(registry['commit-staged'].textContent,/Use these 3 files/);
assert.match(registry['staged-list'].text,/Spreadsheet/);
assert.match(registry['staged-list'].text,/Skipped/);
assert.doesNotMatch(registry['upload-checklist'].text,/Waiting for PCB/);

// Committed files render after staging clears (post-run visibility).
vm.runInContext(`app.upload={id:'up1'};app.uploadFiles=[{name:'board.kicad_pro',size:2048},{name:'board.kicad_sch',size:15360},{name:'board.kicad_pcb',size:4096}];
stagedFiles.clear();renderUploader()`,context);
assert.match(registry['staged-list'].text,/board\.kicad_pcb/);
assert.match(registry['staged-list'].text,/Ready/);

// Single intake: no Add-files button or staged-file picker remains. The dropzone
// is a link handoff, not an upload: names show instantly, the saved folder
// route links them with no dialog, the native picker is the fallback.
// Hidden inputs stay for saved upload-based reports.
assert.equal(vm.runInContext(`typeof buildUploader`,context),'undefined','upload dropzone removed');
assert.equal(vm.runInContext(`typeof chooseProjectFiles`,context),'undefined','Add-files picker removed');
assert.equal(vm.runInContext(`typeof filesFromDrop`,context),'undefined','copying drop walk removed');
assert.equal(vm.runInContext(`typeof linkDropzone`,context),'function','link dropzone exists');
assert.equal(vm.runInContext(`typeof stageDroppedNames`,context),'function','drop stages names with a Link button');
assert.equal(vm.runInContext(`typeof linkViaPicker`,context),'function','picker stays the dialog fallback');
assert.equal(vm.runInContext(`typeof tryAutoLink`,context),'function','saved route links with no dialog');
assert.equal(vm.runInContext(`typeof knownLinkDir`,context),'function','saved folder route is remembered');
context.node=(tag,cls)=>new Element(tag);
const hiddenInputs=vm.runInContext(`hiddenProjectInputs()`,context);
assert.equal(hiddenInputs.length,2);
assert.ok(fs.readFileSync('web/app.js','utf8').includes('project.append(pathInput)'),'typed folder path is placed in the visible setup section, never the collapsed advanced one');
// Boot-blocker guard: the setup section is still detached when path-input is
// moved into it, so any later getElementById('path-input') returns null and
// kills initialize(). The move must reuse one captured reference.
const setupSrc=fs.readFileSync('web/app.js','utf8');
assert.ok(setupSrc.includes('pathInput.prepend'), 'moved path-input is touched through the captured reference');
assert.ok(!setupSrc.includes("$('path-input').prepend"), 'no post-move getElementById lookup of path-input may remain');
assert.ok(hiddenInputs.every(e=>e.hidden===true),'fallback inputs stay invisible');
assert.equal([...hiddenInputs.map(e=>e.id)].sort().join(','),'project-files,zip-file');
assert.ok(hiddenInputs.every(e=>String(e.accept).includes('.kicad_sch')),'fallback inputs keep dotted KiCad suffixes');
console.log('Link intake: single Browse-or-folder intake, hidden fallbacks only passed');

// Removing a committed file surfaces an explicit disabled update action.
vm.runInContext(`stagedFiles.set('board.kicad_sch',{file:{name:'board.kicad_sch',size:15360},name:'board.kicad_sch',size:15360,status:'ready',committable:true,note:''});
stagedFiles.set('board.kicad_pcb',{file:{name:'board.kicad_pcb',size:4096},name:'board.kicad_pcb',size:4096,status:'ready',committable:true,note:''});
renderUploader()`,context);
assert.equal(registry['commit-staged'].hidden,false);
assert.equal(registry['commit-staged'].disabled,true);
assert.match(registry['commit-staged'].textContent,/Update to 2 files/);
console.log('Uploader: checklist, validation, staged list, and committed visibility passed');
