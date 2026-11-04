// PCB-to-Firmware Milestone A: hardware discovery from the reviewed project.
// Create Firmware lives in the review; discovery reuses reviewer evidence and
// never edits project files.
function firmwarePanel(project) {
  if(!project)return null;
  const section=node('section','release-firmware');
  section.append(node('h2','','Firmware'));
  section.append(node('p','field-help','Discover the programmable MCU and its verified connections from this review, then plan firmware in a later step. Your KiCad files are never changed.'));
  const start=node('button','secondary','Create Firmware');start.type='button';
  start.addEventListener('click',()=>openFirmwareDiscovery());
  section.append(start);
  const reportId=app.snapshot.report_id;
  api('/api/firmware-builds/'+encodeURIComponent(reportId)).then(list=>{
    if(!section.isConnected || app.snapshot.report_id!==reportId)return;
    for(const build of (list.builds || []).slice(0,3)){
      const resume=node('button','text-button','Resume discovery ('+build.status+')');resume.type='button';
      resume.addEventListener('click',()=>openFirmwareDiscovery(build.id));
      section.append(resume);
    }
  }).catch(()=>{});
  return section;
}

async function openFirmwareDiscovery(existingId=null) {
  const reportId=app.snapshot.report_id;
  const dialog=claimFixDialog('firmware:'+(existingId || 'new'));
  if(!dialog)return;
  const heading=node('div','dialog-heading'),close=node('button','icon-button','×');close.type='button';
  close.setAttribute('aria-label','Close firmware discovery');close.addEventListener('click',()=>dialog.close());
  heading.append(node('h2','','Firmware discovery'),close);
  const body=node('div');dialog.append(heading,body);document.body.append(dialog);
  dialog.addEventListener('close',()=>{polling=false;dialog.remove();},{once:true});dialog.showModal();
  let job=null,polling=false,working=false,wizIndex=0,wizInit=false;
  function specList(title, items, render) {
    if(!items || !items.length)return;
    body.append(node('h3','',title));
    const list=node('ul');
    for(const item of items.slice(0,24))list.append(node('li','',render(item)));
    body.append(list);
  }
  // Parts sharing kind+value (e.g. expansion connectors J3–J9) render as one
  // family row with an expandable member list; singletons are unchanged.
  const FAMILY_LABELS={i2c_expansion_port:['Expansion connector','Expansion connectors'],swd_debug_port:['SWD debug port','SWD debug ports'],addressable_led:['addressable RGB LED','addressable RGB LEDs'],linear_regulator:['linear regulator','linear regulators'],usb_connector:['USB connector','USB connectors'],spi_flash:['SPI flash chip','SPI flash chips'],rotary_encoder:['rotary encoder','rotary encoders'],passive_two_terminal:['passive part','passive parts']};
  function refRange(refs){
    const sorted=[...refs].sort();
    const parts=sorted.map(r=>r.match(/^([A-Za-z_]+)(\d+)$/));
    if(parts.some(x=>!x))return sorted.join(', ');
    if(parts.some(x=>x[1]!==parts[0][1]))return sorted.join(', ');
    const nums=parts.map(x=>parseInt(x[2],10)).sort((a,b)=>a-b);
    if(nums[nums.length-1]-nums[0]+1===nums.length && nums.length>1)return parts[0][1]+nums[0]+'–'+parts[0][1]+nums[nums.length-1];
    return sorted.join(', ');
  }
  function renderPeripheralFamilies(root, peripherals){
    if(!peripherals.length)return;
    root.append(node('h3','','Identified peripherals'));
    const groups=[], at={};
    for(const p of peripherals){
      const key=[p.part_rule || p.mpn || p.manufacturer || 'part', p.value || ''].join('|');
      if(at[key]===undefined){at[key]=groups.length;groups.push({members:[]});}
      groups[at[key]].members.push(p);
    }
    const list=node('ul');
    for(const g of groups.slice(0,24)){
      const members=g.members;
      if(members.length<2){
        const p=members[0];
        list.append(node('li','',[p.reference, p.value || p.mpn].filter(Boolean).join(' · ')));
        continue;
      }
      const rule=members[0].part_rule || '';
      const labels=FAMILY_LABELS[rule];
      const range=refRange(members.map(p=>p.reference));
      const title=rule==='i2c_expansion_port'
        ?'Expansion connectors '+range+' ('+members.length+' connectors)'
        :(labels?labels[1][0].toUpperCase()+labels[1].slice(1):(members[0].value || 'Parts'))+' '+range+' ('+members.length+')';
      const details=node('details');
      details.append(node('summary','',title));
      for(const p of members){
        const nets=(p.pins || []).map(pin=>pin.net).filter(Boolean).join(' / ');
        details.append(node('p','',[p.reference, p.value || p.mpn, nets && '('+nets+')'].filter(Boolean).join(' · ')));
      }
      const item=node('li','');item.append(details);list.append(item);
    }
    root.append(list);
  }
  function render() {
    if(!dialog.isConnected)return;
    body.replaceChildren();
    if(typeof renderProjectContextBox==='function')renderProjectContextBox(body,reportId);
    if(!job){body.append(node('p','','Starting…'));return;}
    if(job.status==='preparing'){
      body.append(node('p','','Reading the reviewed hardware. Your files are unchanged.'),node('p','field-help',job.message || ''));
      return;
    }
    if(job.status==='blocked'){
      body.append(node('p','review-notice',job.message || 'Discovery failed. Your project files were not changed.'));
      const retry=node('button','secondary','Try again');retry.type='button';
      retry.addEventListener('click',()=>{if(!working)start();});body.append(retry);return;
    }
    const spec=job.spec || {};
    const mcu=spec.mcu || {};
    body.append(node('h3','','Programmable MCU'));
    if(mcu.status==='identified'){
      body.append(node('p','',[mcu.reference, '·', mcu.family, '·', (mcu.declared_value || mcu.declared_mpn || 'declared identity'), '·', mcu.confidence, ' confidence'].join(' ')));
      specList('Evidence', mcu.evidence || [], text=>text);
    }else if(mcu.status==='ambiguous'){
      body.append(node('p','review-notice','More than one component declares an MCU part number. Resolve the open question below before planning firmware.'));
      specList('Candidates', mcu.candidates || [], c=>[c.reference, c.family, c.declared_value || c.declared_mpn].filter(Boolean).join(' · '));
    }else{
      body.append(node('p','review-notice','No programmable MCU could be identified from the declared values and part numbers.'));
    }
    const chains=spec.led_chains || [];
    if(chains.length){
      body.append(node('h3','','LED chains'));
      for(const c of chains.slice(0,12)){
        const bits=['Chain '+c.head+': '+(c.count||0)+' LEDs'];
        if(c.verified){
          bits.push('verified');
          if(c.mcu_pin || c.mcu_net)bits.push('driven by '+(((spec.mcu||{}).reference)||'MCU')+(c.mcu_pin?' pin '+c.mcu_pin:'')+(c.mcu_net?' (net '+c.mcu_net+')':''));
          if(c.driver)bits.push('via '+c.driver+(((c.source_path||[]).length)?' + '+c.source_path.join(', '):''));
          if((c.bridges||[]).length)bits.push(c.bridges.length+' series resistors');
        }else{
          bits.push('unverified — head net '+(c.mcu_net||'unknown'));
        }
        body.append(node('p','',bits.join(' · ')));
      }
      if(chains.length>12)body.append(node('p','field-help','+'+(chains.length-12)+' more chains.'));
    }
    const power=spec.power || {};
    if((power.supply_nets || []).length || (power.ground_nets || []).length)
      body.append(node('p','','Power: '+[...(power.supply_nets || []), ...(power.ground_nets || []).map(n=>n+' (ground)')].join(', ')));
    specList('Communication buses', spec.buses || [], b=>[b.kind, (b.nets || []).join('/'), '·', (b.members || []).join(', ')].filter(Boolean).join(' '));
    renderPeripheralFamilies(body, spec.peripherals || []);
    const gpio=spec.gpio_map || [];
    if(gpio.length){
      const details=node('details');details.append(node('summary','','Pin map · '+gpio.length+' pins'));
      for(const row of gpio.slice(0,48))details.append(node('p','',row.pin+' · '+(row.name || 'unnamed')+' → '+(row.schematic_net || 'unconnected')+(row.pcb_net?' / PCB '+row.pcb_net:'')+(row.pcb_net?(row.match?' (match)':' (mismatch)'):' (no PCB pad)')));
      body.append(details);
    }
    const routed=job.questions || [];
    if(routed.length){
      body.append(node('h3','','Questions'));
      const savedAnswer=need=>(job.answers || []).find(a=>a.need===need);
      const userQs=routed.filter(q=>q.status==='user');
      const settled=routed.filter(q=>q.status!=='user');
      if(userQs.length){
        if(typeof wizIndex!=='number' || wizIndex<0)wizIndex=0;
        if(wizIndex>=userQs.length)wizIndex=userQs.length-1;
        const firstOpen=userQs.findIndex(q=>!savedAnswer(q.need));
        if(!wizInit && firstOpen>0){wizIndex=firstOpen;wizInit=true;}
        const q=userQs[wizIndex];
        const prior=savedAnswer(q.need);
        const remaining=userQs.filter(x=>!savedAnswer(x.need)).length;
        body.append(node('p','field-help','Question '+(wizIndex+1)+' of '+userQs.length+(remaining?(' — '+remaining+' unanswered'):' — all answered')+'. Saved answers survive reload.'));
        body.append(node('p','',q.question || q.need));
        if(q.hint)body.append(node('p','field-help',q.hint));
        if(prior)body.append(node('p','field-help','Saved answer: '+prior.answer+' — edit below to revise.'));
        const input=node('input');input.placeholder='Type the answer…';input.maxLength=2000;
        if(prior)input.value=prior.answer;
        body.append(input);
        let mpn=null;
        if(q.resolves==='mcu' || q.resolves==='peripheral'){
          mpn=node('input');mpn.placeholder='Manufacturer part number (optional)';mpn.maxLength=120;
          if(prior && prior.mpn)mpn.value=prior.mpn;
          body.append(mpn);
        }
        const row=node('div');
        const sendAnswer=async text=>{
          if(working)return;
          if(!text.trim()){body.append(node('p','review-notice','Enter an answer, or press Skip to leave it unresolved.'));return;}
          working=true;
          // Hide the answered question while saving so a stale question and
          // counter never flashes; the fresh record decides the next step.
          row.replaceChildren(node('p','field-help','Saving answer…'));
          try{job=await api('/api/firmware/answer',{job_id:job.id,need:q.need,answer:text,mpn:mpn?mpn.value:''});
            const fresh=(job.questions || []).filter(x=>x.status==='user');
            const at=fresh.findIndex(x=>!((job.answers || []).some(a=>a.need===x.need)));
            wizIndex=at>=0?at:Math.max(0,fresh.length-1);render();}
          catch(error){render();body.append(node('p','review-notice',error.message));}
          finally{working=false;}
        };
        const send=node('button','secondary',remaining>1?'Save and continue':'Save answer');send.type='button';
        send.addEventListener('click',()=>sendAnswer(input.value));
        row.append(send);
        let back=null,skip=null,next=null;
        if(wizIndex>0){
          back=node('button','text-button','Back');back.type='button';
          back.addEventListener('click',()=>{if(!working){wizIndex--;render();}});
          row.append(back);
        }
        if(wizIndex+1<userQs.length){
          next=node('button','text-button','Next');next.type='button';
          next.addEventListener('click',()=>{if(!working){wizIndex++;render();}});
          row.append(next);
        }
        if(!prior){
          skip=node('button','text-button','Skip');skip.type='button';
          skip.addEventListener('click',()=>sendAnswer("I don't know"));
          row.append(skip);
        }
        body.append(row);
      }else{
        body.append(node('p','','All questions answered or resolved — nothing left to ask.'));
      }
      if(settled.length){
        const details=node('details');details.append(node('summary','','Asked and resolved ('+settled.length+')'));
        for(const q of settled){
          const saved=(job.answers || []).find(a=>a.need===q.need);
          if(saved){
            details.append(node('p','','Answered: '+(q.question || q.need)));
            details.append(node('p','field-help','Your answer: '+saved.answer));
          }else if(q.status==='resolved'){
            details.append(node('p','','Answered: '+(q.question || q.need)));
            if(q.fact)details.append(node('p','field-help',q.fact+' ('+(q.provenance || 'recorded')+')'));
          }else if(q.status==='delegated'){
            details.append(node('p','','Investigating: '+q.need));
            details.append(node('p','field-help','Routed to '+(q.target_agent || 'a specialist')+'. '+(q.required_evidence || '')));
          }else{
            details.append(node('p','review-notice',(q.unresolved_information || q.need)+' — preserved unresolved.'));
          }
        }
        body.append(details);
      }
    }else{
      specList('Open questions', spec.open_questions || [], q=>q.need);
    }
    specList('Limits', spec.limits || [], text=>text);
    const plan=job.plan;
    if(plan){
      body.append(node('h3','','Firmware plan'));
      body.append(node('p','','Framework: '+plan.framework));
      if(plan.framework_notes)body.append(node('p','field-help',plan.framework_notes));
      const tc=plan.toolchain || {};
      body.append(node('p','',tc.available?('Toolchain ready: '+(tc.path || tc.binary)):('Toolchain missing: '+tc.binary)));
      body.append(node('p','','Build: '+((plan.build || {}).command || '')));
      specList('Software modules', plan.modules || [], m=>m);
      specList('Drivers', plan.drivers || [], d=>[d.reference, d.part, d.status].filter(Boolean).join(' · '));
      const pending=(plan.decisions || []).filter(d=>d.source!=='answered');
      const settledDecisions=(plan.decisions || []).filter(d=>d.source==='answered');
      specList('Required decisions', pending, d=>d.need);
      specList('Decided with your answers', settledDecisions, d=>d.need+' — yours: '+(d.answer || ''));
      specList('Plan limits', plan.limitations || [], text=>text);
      body.append(node('p','field-help',plan.evidence_basis || ''));
      if(plan.selection_basis)body.append(node('p','field-help','Selected: '+plan.selection_basis));
      if(plan.meta_review){
        const mr=plan.meta_review;
        if(mr.status==='completed'){
          body.append(node('p','','Meta review: agrees with '+plan.framework+' ('+(mr.framework_agreement?'yes':'no')+')'));
          specList('Meta suggestions', mr.suggestions || [], s=>s.area+': '+s.text);
          specList('Meta risks', mr.risks || [], text=>text);
        }else{
          body.append(node('p','field-help','Meta review unavailable: '+(mr.reason || 'no detail')));
        }
      }
      const build=job.build;
      if(build && build.files){
        body.append(node('h3','','Generated firmware'));
        body.append(node('p','',build.message || ''));
        const names=Object.keys(build.files).sort();
        const preview=node('div');
        const showPreview=name=>{
          preview.replaceChildren();
          preview.append(node('h4','',name));
          const closer=node('button','text-button','Close preview');closer.type='button';
          closer.addEventListener('click',()=>preview.replaceChildren());
          const code=node('pre','file-preview',build.files[name]);
          preview.append(closer,code);
        };
        const list=node('ul');
        for(const name of names){
          const item=node('li','');
          const link=node('button','text-button file-preview-link',name+' ('+build.files[name].length+' bytes)');
          link.type='button';
          link.addEventListener('click',()=>showPreview(name));
          item.append(link);
          list.append(item);
        }
        body.append(list,preview);
        const dl=node('a','secondary','Download firmware ZIP');dl.href='/api/firmware-export/'+encodeURIComponent(job.id);dl.download='';
        body.append(dl,node('p','field-help','Downloads the generated project above as a ZIP file. Your KiCad files are unchanged.'));
        specList('Generation checks', build.validation || [], v=>v.check+': '+v.status+' — '+v.detail);
        const cmp=build.compile;
        if(cmp){
          body.append(node('h3','','Compile and verify'));
          let headline=cmp.status+': '+(cmp.reason || 'see errors');
          if(cmp.status==='COMPILED'){
            headline='COMPILED with '+(cmp.toolchain || 'the cross-compiler');
            if(cmp.sizes && cmp.sizes.elf_bytes)headline+=' ('+cmp.sizes.elf_bytes+' bytes elf)';
          }
          body.append(node('p','',headline));
          if(cmp.version)body.append(node('p','field-help',cmp.version));
          if(cmp.status==='COMPILED')body.append(node('p','field-help','The code builds. It has not been hardware tested.'));
          specList('Attempts', cmp.attempts || [], a=>'Attempt '+a.attempt+': '+(a.ok?'ok':'failed')+((a.errors||[]).length?' ('+a.errors.length+' errors)':'')+((a.added_modules||[]).length?' +modules '+a.added_modules.join(','):''));
          const errs=[];for(const a of (cmp.attempts||[]))for(const e of (a.errors||[]))errs.push(e);
          specList('Compiler errors', errs.slice(0,12), e=>(e.file||'')+':'+(e.line||'')+': '+(e.text||''));
        }
        const compileBtn=node('button','secondary',cmp?'Recompile':'Compile and verify');compileBtn.type='button';
        compileBtn.addEventListener('click',async()=>{if(working)return;working=true;compileBtn.disabled=true;const wait=node('p','field-help','Compiling with the cross-compiler…');body.append(wait);try{job=await api('/api/firmware/compile',{job_id:job.id});render();}catch(error){body.append(node('p','review-notice',error.message));}finally{working=false;compileBtn.disabled=false;}});
        body.append(compileBtn,node('p','field-help','Runs make with the real toolchain in an isolated directory and records the result. NOT hardware tested.'));
        const regen=node('button','secondary','Regenerate firmware');regen.type='button';
        regen.addEventListener('click',async()=>{if(working)return;working=true;regen.disabled=true;const wait=node('p','field-help','Generating…');body.append(wait);try{const next=await api('/api/firmware/generate',{job_id:job.id});if(next.ok===false){body.append(node('p','review-notice',next.reason));}else{job=next;render();}}catch(error){body.append(node('p','review-notice',error.message));}finally{working=false;regen.disabled=false;}});
        body.append(regen);
      }else{
        const genBtn=node('button','secondary','Generate firmware');genBtn.type='button';
        genBtn.addEventListener('click',async()=>{if(working)return;working=true;genBtn.disabled=true;const wait=node('p','field-help','Generating…');body.append(wait);try{const next=await api('/api/firmware/generate',{job_id:job.id});if(next.ok===false){body.append(node('p','review-notice',next.reason));}else{job=next;render();}}catch(error){body.append(node('p','review-notice',error.message));}finally{working=false;genBtn.disabled=false;}});
        body.append(genBtn,node('p','field-help','Generate the bare-metal C project, linker script, Makefile, and README from this plan. Only the nRF52 target is supported yet.'));
      }
    }else if(mcu.status==='identified'){
      const planBtn=node('button','secondary','Plan firmware');planBtn.type='button';
      planBtn.addEventListener('click',async()=>{if(working)return;working=true;planBtn.disabled=true;const wait=node('p','','Planning firmware… (includes one bounded Meta review, usually under a minute)');body.append(wait);try{job=await api('/api/firmware/plan',{job_id:job.id});render();}catch(error){body.append(node('p','review-notice',error.message));}finally{working=false;planBtn.disabled=false;}});
      body.append(planBtn,node('p','field-help','Select a compatible framework and plan drivers, pin use, and build steps from the discovery above.'));
    }else{
      body.append(node('p','field-help','Firmware planning needs an identified MCU. Resolve the open question above first.'));
    }
  }
  function job_id(){return job.id;}
  async function poll() {
    if(!polling || !dialog.isConnected)return;
    try{
      const current=await api('/api/firmware/'+job.id);
      job=current;
      if(job.status!=='preparing'){polling=false;}
      render();
    }catch(error){
      if(dialog.isConnected)body.append(node('p','review-notice',error.message));
      polling=false;return;
    }
    if(polling)setTimeout(poll,3000);
  }
  async function start() {
    if(working || app.snapshot.report_id!==reportId)return;
    working=true;render();
    try{
      job=await api('/api/firmware/prepare',{report_id:reportId});
      if(job.status==='preparing'){polling=true;render();setTimeout(poll,3000);}
      else render();
    }catch(error){body.replaceChildren(node('p','review-notice',error.message));}
    finally{working=false;}
  }
  if(existingId){working=true;api('/api/firmware/'+existingId).then(current=>{job=current;working=false;if(job.status==='preparing'){polling=true;setTimeout(poll,3000);}render();}).catch(error=>{working=false;body.replaceChildren(node('p','review-notice',error.message));});}
  else start();
}
