'use strict';

function initializeManufacturing(tabs){
  const button=node('button');button.append(node('span','','Manufacturing'));const count=node('span','tab-count');count.id='count-manufacturing';button.append(count);button.id='tab-manufacturing';button.dataset.tab='manufacturing';button.setAttribute('role','tab');button.setAttribute('aria-controls','panel-manufacturing');
  tabs.insertBefore(button,$('tab-assembly'));
  const panel=node('section');panel.id='panel-manufacturing';panel.hidden=true;panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby','tab-manufacturing');$('results').append(panel);
}

function renderManufacturing(){
  const panel=$('panel-manufacturing');if(!panel || !app.project)return;
  const result=app.project.manufacturing || {}, selection=result.selection || {};
  const main=$('overview-main');if(main && panel.contains(main))$('panel-overview').append(main);
  panel.replaceChildren();
  const heading=node('div','manufacturer-heading');heading.append(node('h2','','Fabrication requirements'),node('p','field-help','Compare the saved board with a manufacturer’s process limits and required order options.'));
  const form=node('form','manufacturer-form');
  for(const [id,label,options,value] of [
    ['maker','Manufacturer',[['','Choose manufacturer'],['jlcpcb','JLCPCB'],['pcbway','PCBWay · standard service']],selection.manufacturer || ''],
    ['maker-copper','Copper · all layers',[['1','1 oz (35 µm)'],['2','2 oz (70 µm)']],selection.copper_oz || '1'],
    ['maker-finish','Surface finish',[['enig','ENIG'],['hasl','Lead-free HASL'],['osp','OSP']],selection.finish || 'enig']
  ]){
    const field=node('div');const lab=node('label','',label);lab.htmlFor=id;const select=node('select');select.id=id;
    for(const [key,text] of options){const option=node('option','',text);option.value=key;select.append(option);}select.value=value;
    select.addEventListener('change',()=>{results.hidden=true;review.hidden=true;message.textContent='Options changed. Click Check manufacturer to apply them.';});field.append(lab,select);form.append(field);
  }
  const check=node('button','primary','Check manufacturer');check.type='submit';check.dataset.designAction='manufacturer';check.disabled=app.busy;
  form.append(check);
  const message=node('p','field-help');message.setAttribute('role','status');
  const results=node('div','manufacturer-results');
  let submitting=false;
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(submitting || app.busy)return;submitting=true;check.disabled=true;check.textContent='Checking…';message.textContent='Reading the saved board measurements…';
    for(const input of form.querySelectorAll('select'))input.disabled=true;
    try{
      await api('/api/manufacturing',{report_id:app.snapshot.report_id,selection:{manufacturer:$('maker').value,copper_oz:$('maker-copper').value,finish:$('maker-finish').value}});
      await refresh();
    }catch(error){message.textContent=error.message;check.disabled=false;check.textContent='Check manufacturer';for(const input of form.querySelectorAll('select'))input.disabled=false;}finally{submitting=false;}
  });
  panel.append(heading,form,message,results);
  const review=node('div');review.id='manufacturer-review';panel.insertBefore(review,results);
  if(overviewUI.section!=='manufacturing')chooseReviewSection('manufacturing');else renderOverview();
  if(!selection.manufacturer){results.append(node('p','manufacturer-empty','Checks include layers, thickness, trace widths, via size, vias inside solder pads and reported pad gaps. Your choice is remembered for this project.'));return;}
  const status=node('div',`manufacturer-status ${result.status || ''}`);status.append(node('h3','',result.summary || 'Click Check manufacturer to begin.'));
  if(result.measurements?.status==='measured'){
    const m=result.measurements;status.append(node('p','',`${m.layers ?? '?'} copper layers · ${m.thickness_mm ?? '?'} mm thick · ${Array.isArray(m.vias)?m.vias.reduce((sum,v)=>sum+(Number(v.count)||0),0):'—'} vias · ${m.trace_count ?? '—'} trace segments`));
    status.append(node('p','field-help','This is a partial fabrication check. Unchecked items are listed below.'));
  }
  results.append(status);
  if(!result.checks?.length)return;
  const labels={supported:'Within limit',option:'Order option',outside:'Outside profile',unknown:'Not verified'};
  const wrap=node('div','manufacturer-table-wrap');const table=node('table','manufacturer-table');const head=node('thead');const hr=node('tr');for(const text of ['Check','Your board / order','Published requirement','Result'])hr.append(node('th','',text));head.append(hr);table.append(head);const body=node('tbody');
  for(const row of result.checks){
    const tr=node('tr');tr.append(node('th','',row.label),node('td','',row.actual));const requirement=node('td','',row.limit);if(row.advice && ['outside','unknown'].includes(row.status))requirement.append(node('p','field-help',row.advice));tr.append(requirement);const verdict=node('td');verdict.append(node('span','manufacturer-badge '+row.status,labels[row.status] || 'Not verified'));tr.append(verdict);body.append(tr);
  }
  table.append(body);wrap.append(table);results.append(wrap);
  const vip=result.checks.find(c=>c.id==='via_in_pad');
  if(vip){
    const warning=node('section','manufacturer-order');warning.append(node('h3','','Vias inside solder pads'),node('p','',vip.actual),node('p','','Open holes can draw solder away from the joint during assembly. Small-via sizing alone does not address this risk.'),node('p','',vip.advice));
    const details=node('details');details.append(node('summary','','Detected pads and via locations'));
    const locations=node('ul');for(const site of vip.locations || [])locations.append(node('li','',`${site.reference || 'Unknown part'} pad ${site.pad ?? '—'} · ${(site.sides || []).join('/') || 'Unknown'} side · ${Number.isFinite(site.x_mm)?site.x_mm.toFixed(3):'—'}, ${Number.isFinite(site.y_mm)?site.y_mm.toFixed(3):'—'} mm · drill ${site.drill_mm ?? '—'} mm`));details.append(locations);warning.append(details);
    const source=node('a','text-link','Manufacturer’s via filling guidance ↗');source.href=vip.source;source.target='_blank';source.rel='noreferrer';warning.append(source);results.append(warning);
  }
  const order=node('section','manufacturer-order');order.append(node('h3','','Settings to use when ordering'));const list=node('ul');for(const text of result.order_settings || [])list.append(node('li','',text));order.append(list);
  if(result.status==='outside')order.prepend(node('p','manufacturer-warning','Resolve the items outside this profile before using these order settings.'));
  const download=node('button','secondary','Download order checklist');download.type='button';download.addEventListener('click',()=>{
    const text=[app.project.provenance?.project_name || app.project.archive,(result.profile?.name || 'Manufacturer')+' · '+(result.profile?.process || 'Process not recorded'),'Checked against published limits on '+(result.verified || 'date not recorded'),'Saved run: '+app.snapshot.report_id,'',result.summary,'',...(result.order_settings || []),'',...result.checks.map(c=>`${c.label || 'Check'}: ${labels[c.status] || 'Not verified'} — ${c.actual || 'Not recorded'}; ${c.limit || 'Not recorded'}. ${c.advice || ''}\nSource: ${c.source || 'Not recorded'}${c.verified?' (checked '+c.verified+')':''}`),'',...(vip?.locations || []).map(s=>`${s.reference || 'Unknown part'} pad ${s.pad ?? '—'}: ${(s.sides || []).join('/')} side; via at ${s.x_mm ?? '—'}, ${s.y_mm ?? '—'} mm; drill ${s.drill_mm ?? '—'} mm`),'',...(result.limits || []),'',(result.profile?.url || '')].join('\n');
    const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));const link=node('a');link.href=url;link.download='manufacturer-checklist.txt';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });order.append(download);results.append(order);
  for(const [title,lines] of [['KiCad settings for this process',result.kicad_settings],['What this check does not cover',result.limits]]){
    const details=node('details','manufacturer-details');details.append(node('summary','',title));const ul=node('ul');for(const text of lines || [])ul.append(node('li','',text));if(!lines?.length)ul.append(node('li','','No verified KiCad settings for this combination.'));details.append(ul);results.append(details);
  }
  const source=node('p','field-help');source.append(document.createTextNode(`Profile verified ${result.verified || 'date not recorded'} · `));const a=node('a','text-link',(result.profile?.name || 'Manufacturer')+' official capabilities ↗');a.href=(result.profile?.url || '');a.target='_blank';a.rel='noreferrer';source.append(a);results.append(source);
}
