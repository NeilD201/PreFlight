// Create Firmware entry + discovery dialog: entry point exists, start posts a
// prepare job, validated discovery renders the MCU from reviewer evidence,
// and saved builds offer resume. Mirrors tests/test_fix_design_ui.js.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
  constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.listeners={};this.isConnected=true;this.checked=false;this.style={};}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  addEventListener(type,fn){this.listeners[type]=fn;}
  setAttribute(){}
  showModal(){}
  close(){this.isConnected=false;this.listeners.close?.();}
  remove(){this.isConnected=false;}
}
function descendants(root){return [root,...root.children.flatMap(descendants)];}
function setup(responder){
  const body=new Element('body'),calls=[];
  const context=vm.createContext({console,setTimeout,document:{body,createTextNode:text=>new Element('text',text)},
    reviewInstruction:text=>text,node:(tag,_class,text='')=>new Element(tag,text),app:{snapshot:{report_id:'r1'},project:{},config:{key_configured:true},busy:false},
    designUI:{chatBusy:false},openStepChat:(...args)=>calls.push({path:'investigate',args}),openSettings:()=>calls.push({path:'settings'}),
    api:async(path,data)=>{calls.push({path,data});return responder(path,data,body);},updateSavedProject:()=>{calls.push({path:'update-review'});}});
  vm.runInContext(fs.readFileSync('web/file_fixes.js','utf8'),context);
  vm.runInContext(fs.readFileSync('web/firmware.js','utf8'),context);
  return {context,body,calls,run:code=>vm.runInContext(code,context)};
}
const validatedSpec=()=>({id:'fw1',status:'validated',spec:{mcu:{status:'identified',reference:'U1',family:'nRF52',declared_value:'nRF52840_QIAA',declared_mpn:'nRF52840-QIAA',confidence:'high',evidence:['Declared MPN "nRF52840-QIAA"']},power:{supply_nets:['VDD'],ground_nets:['GND']},buses:[],peripherals:[],gpio_map:[],open_questions:[],limits:[]},message:'done'});
(async()=>{
  const shown=setup(()=>({builds:[]}));
  assert.equal(typeof shown.run('firmwarePanel'), 'function','firmware entry point exists');
  const panel=shown.run('firmwarePanel({})');
  assert.ok(panel,'panel renders for a loaded project');
  const start=descendants(panel).find(e=>e.tag==='button' && e.text==='Create Firmware');
  assert.ok(start,'Create Firmware button is offered');
  await start.listeners.click();
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.ok(shown.calls.some(c=>c.path==='/api/firmware/prepare' && c.data.report_id==='r1'),'start posts a prepare job for the open review');
  const planned=setup((path,data)=>{
    if(path==='/api/firmware/prepare')return {id:'fw1',status:'validated',spec:{mcu:{status:'identified',reference:'U1',family:'nRF52',declared_value:'nRF52840',confidence:'medium',evidence:[]},power:{},buses:[],peripherals:[],gpio_map:[],open_questions:[],limits:[]},message:'done'};
    if(path==='/api/firmware/plan')return {id:'fw1',status:'validated',plan:{framework:'Bare-metal C (ARM Cortex-M)',framework_notes:'',toolchain:{binary:'arm-none-eabi-gcc',available:true,path:'/usr/bin/arm-none-eabi-gcc'},modules:['hal_gpio'],drivers:[],build:{command:'make TARGET=nrf52'},decisions:[],limitations:[],evidence_basis:'U1'},message:'done'};
    return {builds:[]};
  });
  const panel3=planned.run('firmwarePanel({})');
  await descendants(panel3).find(e=>e.tag==='button' && e.text==='Create Firmware').listeners.click();
  await new Promise(resolve=>setTimeout(resolve,50));
  const planBtn=descendants(planned.body).find(e=>e.tag==='button' && e.text==='Plan firmware');
  assert.ok(planBtn,'validated discovery with an identified MCU offers planning');
  await planBtn.listeners.click();
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.ok(planned.calls.some(c=>c.path==='/api/firmware/plan' && c.data.job_id==='fw1'),'planning posts the discovery id');
  assert.ok(descendants(planned.body).some(e=>e.tag==='p' && String(e.text).startsWith('Framework:')),'the framework plan renders');
  const asked=setup((path,data)=>{
    const purpose={need:'What should the firmware do?',why:'Intent needed.',resolves:'purpose',scope:{},status:'user',question:'What should the firmware do?',hint:'Why it matters.',decision_id:'qc-1',originating_agent:'firmware',asked:{question:'What should the firmware do?'}};
    if(path==='/api/firmware/prepare')return {id:'fw1',status:'validated',spec:{mcu:{status:'identified',reference:'U1',family:'nRF52',declared_value:'nRF52840',confidence:'high',evidence:[]},power:{},buses:[],peripherals:[],gpio_map:[],open_questions:[{need:purpose.need}],limits:[]},questions:[purpose],answers:[],message:'done'};
    if(path==='/api/firmware/answer')return {id:'fw1',status:'validated',spec:{mcu:{status:'identified',reference:'U1',family:'nRF52',declared_value:'nRF52840',confidence:'high',evidence:[]},power:{},buses:[],peripherals:[],gpio_map:[],open_questions:[],limits:[],stated_purpose:data.answer},questions:[{need:purpose.need,resolves:'purpose',scope:{},status:'resolved',fact:'Documented: '+data.answer,provenance:'already recorded in previous answer'}],answers:[{need:purpose.need,answer:data.answer}],message:'saved'};
    return {builds:[]};
  });
  const panel4=asked.run('firmwarePanel({})');
  await descendants(panel4).find(e=>e.tag==='button' && e.text==='Create Firmware').listeners.click();
  await new Promise(resolve=>setTimeout(resolve,50));
  const input=descendants(asked.body).find(e=>e.tag==='input');
  assert.ok(input && input.placeholder==='Type the answer…','open user questions render an answer box');
  input.value='Log temperature.';
  await descendants(asked.body).find(e=>e.tag==='button' && e.text==='Save answer').listeners.click();
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.ok(asked.calls.some(c=>c.path==='/api/firmware/answer' && c.data.need==='What should the firmware do?' && c.data.answer==='Log temperature.'),'saving posts the need and answer');
  assert.ok(descendants(asked.body).some(e=>e.tag==='p' && String(e.text).includes('Answered:')),'the answered question shows its resolved state');
  const generated=setup((path,data)=>{
    const plan={framework:'Bare-metal C (ARM Cortex-M)',framework_notes:'',selection_basis:'nRF52',toolchain:{binary:'arm-none-eabi-gcc',available:true,path:'/usr/bin/arm-none-eabi-gcc'},modules:['hal_gpio'],drivers:[],build:{command:'make TARGET=nrf52'},decisions:[{need:'What should turning the encoders do?',why:'Intent.',source:'answered',answer:'Volume keys.'}],limitations:[],evidence_basis:'U1'};
    const spec={mcu:{status:'identified',reference:'U1',family:'nRF52',declared_value:'nRF52840',confidence:'high',evidence:[]},power:{},buses:[],peripherals:[],gpio_map:[],open_questions:[],limits:[]};
    if(path==='/api/firmware/prepare')return {id:'fw1',status:'validated',spec,plan,questions:[],answers:[],message:'done'};
    if(path==='/api/firmware/generate')return {id:'fw1',status:'validated',spec,plan,questions:[],answers:[],build:{files:{'README.md':'hi','src/main.c':'int main(){return 0;}'},validation:[{check:'mcu',status:'pass',detail:'U1'}],message:'Generated 2 files.'},message:'done'};
    return {builds:[]};
  });
  const panel5=generated.run('firmwarePanel({})');
  await descendants(panel5).find(e=>e.tag==='button' && e.text==='Create Firmware').listeners.click();
  await new Promise(resolve=>setTimeout(resolve,50));
  await descendants(generated.body).find(e=>e.tag==='button' && e.text==='Generate firmware').listeners.click();
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.ok(generated.calls.some(c=>c.path==='/api/firmware/generate' && c.data.job_id==='fw1'),'generating posts the discovery id');
  assert.ok(descendants(generated.body).some(e=>e.tag==='button' && String(e.text).includes('src/main.c')),'the generated file list renders');
  assert.ok(descendants(generated.body).some(e=>e.tag==='li' && String(e.text).includes('Volume keys.')),'answered decisions show the saved answer in the plan');
  assert.ok(!descendants(generated.body).some(e=>e.tag==='li' && String(e.text)==='What should turning the encoders do?'),'answered decisions no longer look required');
  await descendants(generated.body).find(e=>e.tag==='button' && String(e.text).includes('src/main.c')).listeners.click();
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.ok(descendants(generated.body).some(e=>e.tag==='pre' && String(e.text).includes('int main')),'clicking a file previews its contents');
  const zip=descendants(generated.body).find(e=>e.tag==='a' && String(e.text).includes('Download firmware ZIP'));
  assert.ok(zip && String(zip.href).includes('/api/firmware-export/fw1'),'the download link points at the firmware export endpoint');
  const compiled=setup((path,data)=>{
    const spec={mcu:{status:'identified',reference:'U1',family:'nRF52',declared_value:'nRF52840',confidence:'high',evidence:[]},power:{},buses:[],peripherals:[],gpio_map:[],open_questions:[],limits:[]};
    const plan={framework:'Bare-metal C (ARM Cortex-M)',framework_notes:'',selection_basis:'nRF52',toolchain:{binary:'arm-none-eabi-gcc',available:true,path:'/usr/bin/arm-none-eabi-gcc'},modules:['hal_gpio'],drivers:[],build:{command:'make'},decisions:[],limitations:[],evidence_basis:'U1'};
    const build={files:{'README.md':'hi'},validation:[],message:'Generated 1 file.'};
    if(path==='/api/firmware/prepare')return {id:'fw1',status:'validated',spec,plan,questions:[],answers:[],message:'done'};
    if(path==='/api/firmware/generate')return {id:'fw1',status:'validated',spec,plan,questions:[],answers:[],build,message:'done'};
    if(path==='/api/firmware/compile')return {id:'fw1',status:'validated',spec,plan,questions:[],answers:[],build:{...build,compile:{status:'COMPILED',attempts:[{attempt:1,ok:true,errors:[],warnings:0}],sizes:{elf_bytes:1772},toolchain:'/usr/bin/arm-none-eabi-gcc',version:'arm-none-eabi-gcc 15.3.1'}},message:'done'};
    return {builds:[]};
  });
  const panel6=compiled.run('firmwarePanel({})');
  await descendants(panel6).find(e=>e.tag==='button' && e.text==='Create Firmware').listeners.click();
  await new Promise(resolve=>setTimeout(resolve,50));
  await descendants(compiled.body).find(e=>e.tag==='button' && e.text==='Generate firmware').listeners.click();
  await new Promise(resolve=>setTimeout(resolve,50));
  await descendants(compiled.body).find(e=>e.tag==='button' && e.text==='Compile and verify').listeners.click();
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.ok(compiled.calls.some(c=>c.path==='/api/firmware/compile' && c.data.job_id==='fw1'),'compiling posts the discovery id');
  assert.ok(descendants(compiled.body).some(e=>e.tag==='p' && String(e.text).includes('COMPILED')),'the compile result renders');
  const wizard=setup((path,data)=>{
    const q1={need:'What should the firmware do?',why:'Intent.',resolves:'purpose',scope:{},status:'user',question:'What should the firmware do?',hint:'',decision_id:'qc-1',originating_agent:'firmware'};
    const q2={need:'What should turning the encoders SW1, SW2 do?',why:'Behavior.',resolves:'encoder_behavior',scope:{components:['SW1','SW2'],nets:[]},status:'user',question:'What should turning the encoders do?',hint:'',decision_id:'qc-2',originating_agent:'firmware'};
    const spec={mcu:{status:'identified',reference:'U2',family:'RP2040',declared_value:'RP2040',confidence:'high',evidence:[]},power:{},buses:[],peripherals:[],gpio_map:[],led_chains:[{head:'D1',count:3,verified:true,mcu_net:'RGB',mcu_pin:'4 GPIO2',driver:'U8',source_path:['R10'],bridges:['R10'],unresolved:[]}],open_questions:[{need:q1.need},{need:q2.need}],limits:[]};
    if(path==='/api/firmware/prepare')return {id:'fw1',status:'validated',spec,questions:[q1,q2],answers:[],message:'done'};
    if(path==='/api/firmware/answer'){
      const answered={need:data.need,answer:data.answer};
      const rest=data.need===q1.need?[q2]:[];
      return {id:'fw1',status:'validated',spec:{...spec,open_questions:rest.map(q=>({need:q.need}))},questions:[q1,q2],answers:[answered],message:'saved'};
    }
    return {builds:[]};
  });
  const panelW=wizard.run('firmwarePanel({})');
  await descendants(panelW).find(e=>e.tag==='button' && e.text==='Create Firmware').listeners.click();
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.ok(descendants(wizard.body).some(e=>e.tag==='h3' && e.text==='LED chains'),'the merged chain summary renders');
  assert.ok(descendants(wizard.body).some(e=>e.tag==='p' && String(e.text).includes('Chain D1: 3 LEDs') && String(e.text).includes('verified') && String(e.text).includes('4 GPIO2') && String(e.text).includes('U8')),'the chain names its driver, pin, and bridge');
  const inputs=descendants(wizard.body).filter(e=>e.tag==='input' && e.placeholder==='Type the answer…');
  assert.equal(inputs.length,1,'exactly one question is asked at a time');
  assert.ok(descendants(wizard.body).some(e=>e.tag==='p' && String(e.text).includes('Question 1 of 2')),'the wizard names the current step and total');
  assert.ok(descendants(wizard.body).some(e=>e.tag==='button' && e.text==='Skip'),'skipping is offered');
  assert.ok(descendants(wizard.body).some(e=>e.tag==='button' && e.text==='Next'),'the next question is reachable without answering');
  await descendants(wizard.body).find(e=>e.tag==='button' && e.text==='Next').listeners.click();
  assert.ok(descendants(wizard.body).some(e=>e.tag==='p' && String(e.text).includes('Question 2 of 2')),'Next advances one step');
  assert.ok(descendants(wizard.body).some(e=>e.tag==='button' && e.text==='Back'),'Back returns to the previous question');
  await descendants(wizard.body).find(e=>e.tag==='button' && e.text==='Back').listeners.click();
  inputs.push(...descendants(wizard.body).filter(e=>e.tag==='input' && e.placeholder==='Type the answer…'));
  const first=descendants(wizard.body).filter(e=>e.tag==='input' && e.placeholder==='Type the answer…');
  assert.equal(first.length,1,'Back keeps a single visible question');
  first[0].value='Volume keys.';
  await descendants(wizard.body).find(e=>e.tag==='button' && String(e.text).startsWith('Save and continue')).listeners.click();
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.ok(wizard.calls.some(c=>c.path==='/api/firmware/answer' && c.data.answer==='Volume keys.'),'saving posts the wizard answer');
  assert.ok(descendants(wizard.body).some(e=>e.tag==='p' && String(e.text).includes('unanswered')),'the wizard resumes at the remaining question');
  const resumed=setup(()=>({builds:[{id:'fw9',status:'validated',message:'done'}]}));
  const panel2=resumed.run('firmwarePanel({})');
  await new Promise(resolve=>setTimeout(resolve,50));
  const resume=descendants(panel2).find(e=>e.tag==='button' && String(e.text).startsWith('Resume'));
  assert.ok(resume,'a saved discovery offers resume');
  assert.match(resume.text,/validated/,'resume names the saved build status');
  const family=setup((path)=>{
    if(path==='/api/firmware/prepare'){
      const peripherals=['J3','J4','J5','J6','J7','J8','J9'].map(ref=>({reference:ref,value:'Conn_01x04_Socket',mpn:'',manufacturer:'',part_rule:'i2c_expansion_port',package:'PinHeader_1x04',pins:[{pin:'1',name:'',net:'+3.3V'},{pin:'4',name:'',net:'GND'}],identified:true,evidence:[]}));
      peripherals.push({reference:'U3',value:'LD1117V',mpn:'',manufacturer:'',part_rule:'linear_regulator',package:'SOT-223',pins:[],identified:true,evidence:[]});
      return {id:'fw1',status:'validated',spec:{mcu:{status:'identified',reference:'U2',family:'RP2040',declared_value:'RP2040',confidence:'high',evidence:[]},power:{},buses:[],peripherals,gpio_map:[],open_questions:[],limits:[]},message:'done'};
    }
    return {builds:[]};
  });
  const panelF=family.run('firmwarePanel({})');
  await descendants(panelF).find(e=>e.tag==='button' && e.text==='Create Firmware').listeners.click();
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.ok(descendants(family.body).some(e=>e.tag==='summary' && String(e.text)==='Expansion connectors J3–J9 (7 connectors)'),'the connector family renders as one row');
  assert.ok(descendants(family.body).some(e=>e.tag==='p' && String(e.text).includes('J5')),'family members stay inspectable');
  assert.ok(descendants(family.body).some(e=>e.tag==='li' && String(e.text).includes('U3')),'singleton peripherals render unchanged');
  console.log('firmware UI: all assertions passed');
})().catch(error=>{console.error(error);process.exit(1);});
