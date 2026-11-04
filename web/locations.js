'use strict';

function boardSites(group,project){
  const visual=project.workspace?.board_visual || {}, index=visual.item_locations || {}, sites=new Map();
  const manufacturing=project.action_plan?.actions?.find(a=>a.group_id===group.id)?.manufacturer_check;
  for(const match of manufacturing?.locations || []){
    const items=[index[match.via_uuid],index[match.pad_uuid]].filter(Boolean);
    if(items.length===2)sites.set(match.via_uuid+':'+match.pad_uuid,{key:match.via_uuid+':'+match.pad_uuid,items,sides:match.sides,findings:['Via hole overlaps a component solder pad. Confirm filling and copper capping before assembly.'],issue_ids:[],basis:'Via-in-pad geometry'});
  }
  if(sites.size)return [...sites.values()];
  for(const fid of group.issue_ids || []){
    const issue=project.issues?.[/^F\d+$/.test(fid)?Number(fid.slice(1))-1:-1];
    if(issue?.source!=='kicad_drc' || issue.excluded)continue;
    const seen=new Set(),items=[];
    for(const raw of issue.items || []){
      if(seen.has(raw.uuid))continue;
      const shape=index[raw.uuid];
      if(shape){items.push({...shape,description:raw.description});seen.add(raw.uuid);}
      else if(Number.isFinite(raw.pos?.x) && Number.isFinite(raw.pos?.y)){
        const layers=[...(raw.description || '').matchAll(/\b(?:F|B|In\d+)\.Cu\b/g)].map(m=>m[0]);
        // Native through vias belong to every intervening layer, not just F.Cu.
        const through=/^Via\b/.test(raw.description || '') && layers.includes('F.Cu') && layers.includes('B.Cu');
        items.push({uuid:raw.uuid,point:[raw.pos.x,raw.pos.y],kind:'reported',label:raw.description || 'Reported item',layers:through?visual.copper_layers || layers:layers,approximate:true});seen.add(raw.uuid);
      }
    }
    if(!items.length)continue;
    const key=items.map(p=>p.uuid || JSON.stringify(p.point)).sort().join('|');
    const site=sites.get(key) || {key,items,findings:[],issue_ids:[],basis:'KiCad finding'};
    site.findings.push(issue.message);site.issue_ids.push(fid);sites.set(key,site);
  }
  if(sites.size)return [...sites.values()];
  const path=(project.signal_paths?.paths || []).find(p=>p.id===group.signal_path_id);
  if(path){
    const nets=path.kind==='rf'?[path.nets.at(-1)?.net]:path.nets.map(n=>n.net);
    const items=Object.values(index).filter(p=>p.kind==='pad' && nets.includes(p.net));
    const sites=[];
    if(items.length)sites.push({key:path.id,items,findings:[path.kind==='rf'?'Highlighted pads are on the output side of the matching network. The antenna endpoint is unresolved.':'Highlighted pads belong to the sensitive input paths. A routing defect has not been established.'],issue_ids:group.issue_ids || [],basis:'Circuit path to review',context:true});
    // Older saved reports may not yet contain the RF path's detached-artwork
    // list. The board visual is refreshed against the checked PCB hash when
    // the project loads, so use its geometry index as a safe fallback.
    const detachedRecords=path.unnetted_copper_outside_outline || Object.values(index)
      .filter(item=>item.kind==='graphic' && item.outside_outline && !item.net)
      .map(item=>({uuid:item.uuid}));
    const detached=detachedRecords.map(record=>index[record.uuid]).filter(Boolean);
    detached.forEach((item,i)=>sites.push({key:`${path.id}:unnetted-copper:${i}`,items:[item],
      findings:['Unnetted copper drawing outside Edge.Cuts. It is not connected to the RF output and cannot serve as an on-board antenna.'],
      issue_ids:group.issue_ids || [],basis:'Unconnected copper drawing',context:true}));
    if(sites.length)return sites;
  }
  const sheet=project.workspace?.schematic_visual || {};
  const pins=schematicTargets(group,sheet,project).filter(p=>p.kind==='pin');
  for(const pin of pins){
    const items=(visual.pad_locations?.[pin.reference]?.[pin.pin] || []).map(uuid=>index[uuid]).filter(Boolean);
    if(items.length)sites.set(pin.reference+':'+pin.pin,{key:pin.reference+':'+pin.pin,items,findings:[],issue_ids:group.issue_ids || [],basis:'Pin-to-pad location'});
  }
  // Named pads without a schematic symbol pin (tab, MP) still resolve on copper.
  for(const pin of group.chain_targets || []){
    if(!pin?.reference || pins.some(p=>p.reference===pin.reference && String(p.pin)===String(pin.pin)))continue;
    const items=(visual.pad_locations?.[pin.reference]?.[pin.pin] || []).map(uuid=>index[uuid]).filter(Boolean);
    if(items.length)sites.set(pin.reference+':'+pin.pin,{key:pin.reference+':'+pin.pin,items,findings:['Footprint location for the named pad; no schematic symbol pin exists.'],issue_ids:group.issue_ids || [],basis:'Pad location'});
  }
  // A finding about a component shows that component's footprint when nothing
  // pin-specific resolved, instead of rendering an empty board view.
  if(!sites.size)for(const ref of group.components || []){
    const pads=visual.pad_locations?.[ref] || {};
    const items=Object.values(pads).flat().map(uuid=>index[uuid]).filter(Boolean);
    if(items.length)sites.set(ref,{key:ref,items,findings:[],issue_ids:group.issue_ids || [],basis:'Component footprint'});
  }
  return [...sites.values()];
}

function locationPoints(item){
  if(item.points?.length)return item.points;
  const [x,y]=item.point,r=item.radius_mm || .25;return [[x-r,y-r],[x+r,y+r]];
}

function plotPoint(point,view){return [view.offset[0]+point[0]*(view.mirrored?-1:1),view.offset[1]+point[1]];}

function locationBounds(items,view){
  const points=items.flatMap(locationPoints).map(p=>plotPoint(p,view));
  if(!points.length)return view.bounds.slice();
  const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
  const x=Math.min(...xs),y=Math.min(...ys),w=Math.max(...xs)-x,h=Math.max(...ys)-y;
  const width=Math.max(2.4,w+Math.max(.8,w*.35)),height=Math.max(2.4,h+Math.max(.8,h*.35));
  return [x+(w-width)/2,y+(h-height)/2,width,height];
}

function preferredBoardSide(site,current='front'){
  if(site.sides?.length){if(site.sides.includes(current==='front'?'F':'B'))return current;return site.sides.includes('F')?'front':'back';}
  const layers=[...new Set(site.items.flatMap(p=>p.layers || []))];
  if(layers.includes(current==='front'?'F.Cu':'B.Cu'))return current;
  if(layers.includes('F.Cu'))return 'front';if(layers.includes('B.Cu'))return 'back';return current;
}

function locationLabel(site){
  const first=site.items[0],point=first.point;
  return site.items.length===1?`${first.label}${first.net?' · '+first.net:''} · ${point[0].toFixed(3)}, ${point[1].toFixed(3)} mm`:site.items.map(p=>p.label).join(' ↔ ');
}

function svgNode(tag,attrs={}){
  const el=document.createElementNS('http://www.w3.org/2000/svg',tag);
  for(const [key,value] of Object.entries(attrs))el.setAttribute(key,String(value));return el;
}

function drawLocation(svg,item,view,number,context=false){
  const [x,y]=plotPoint(item.point,view),color=context?'#007e9a':'#c94f1c';
  const group=svgNode('g',{'data-location-uuid':item.uuid || '',class:'location-object'});
  const title=svgNode('title');title.textContent=item.label+(item.net?' · '+item.net:'');group.append(title);
  let shape;
  if(item.kind==='track' || item.kind==='graphic')shape=svgNode('polyline',{points:item.points.map(p=>plotPoint(p,view).join(',')).join(' '),'stroke-width':Math.max(item.width_mm || .1,.08),fill:'none',stroke:color});
  else if(item.radius_mm)shape=svgNode('circle',{cx:x,cy:y,r:item.radius_mm,fill:color+'33',stroke:color,'stroke-width':2,'vector-effect':'non-scaling-stroke'});
  else if(item.points?.length)shape=svgNode('polygon',{points:item.points.map(p=>plotPoint(p,view).join(',')).join(' '),fill:color+'33',stroke:color,'stroke-width':2,'vector-effect':'non-scaling-stroke'});
  else shape=svgNode('circle',{cx:x,cy:y,r:.35,fill:'none',stroke:color,'stroke-width':2,'stroke-dasharray':'4 3','vector-effect':'non-scaling-stroke'});
  group.append(shape);
  if(item.kind==='via' && item.drill_mm)group.append(svgNode('circle',{cx:x,cy:y,r:item.drill_mm/2,fill:'#fff',stroke:color,'stroke-width':1,'vector-effect':'non-scaling-stroke'}));
  svg.append(group);return {point:[x,y],number,color};
}

function enableViewport(svg,initial,full,onChange=()=>{}){
  let box=initial.slice(),drag=null;
  const update=()=>{svg.setAttribute('viewBox',box.join(' '));onChange(box);};
  const zoom=factor=>{const [x,y,w,h]=box;if(w*factor<.15 || w*factor>3000)return;box=[x+w*(1-factor)/2,y+h*(1-factor)/2,w*factor,h*factor];update();};
  svg.addEventListener('pointerdown',e=>{if(e.button!==0)return;drag={x:e.clientX,y:e.clientY,box:box.slice()};svg.setPointerCapture(e.pointerId);});
  svg.addEventListener('pointermove',e=>{if(!drag)return;const scale=Math.min(svg.clientWidth/drag.box[2],svg.clientHeight/drag.box[3]);box=[drag.box[0]-(e.clientX-drag.x)/scale,drag.box[1]-(e.clientY-drag.y)/scale,...drag.box.slice(2)];update();});
  for(const event of ['pointerup','pointercancel'])svg.addEventListener(event,()=>drag=null);
  svg.addEventListener('wheel',e=>{if(!e.ctrlKey && !e.metaKey)return;e.preventDefault();zoom(e.deltaY>0?1.15:.87);},{passive:false});
  const controls=node('div','location-controls');
  for(const [label,fn] of [['−',()=>zoom(1.35)],['+',()=>zoom(.74)],['Fit location',()=>{box=initial.slice();update();}],['Whole board',()=>{box=full.slice();update();}]]){
    const button=node('button','text-button',label);button.type='button';button.setAttribute('aria-label',label==='−'?'Zoom out':label==='+'?'Zoom in':label);button.addEventListener('click',fn);controls.append(button);
  }
  update();return controls;
}

function renderBoardLocation(box,group,visual,sites){
  if(overviewUI.boardSiteScope!==app.snapshot.report_id+':'+group.id){overviewUI.boardSiteScope=app.snapshot.report_id+':'+group.id;overviewUI.boardSite=0;overviewUI.side=preferredBoardSide(sites[0],overviewUI.side);}
  overviewUI.boardSite=Math.min(overviewUI.boardSite || 0,sites.length-1);
  if(overviewUI.preferredPin){const index=sites.findIndex(s=>s.items.some(p=>p.reference+':'+p.pin===overviewUI.preferredPin));if(index>=0)overviewUI.boardSite=index;overviewUI.preferredPin=null;}
  const site=sites[overviewUI.boardSite],view=visual.views[overviewUI.side];
  const action=app.project.action_plan?.actions?.find(a=>a.group_id===group.id);
  const referenceOnly=action?.disposition==='no_change' || action?.reference_only || (['supported','option'].includes(action?.manufacturer_check?.status) && !action?.manufacturer_check?.locations?.length);
  const nav=node('div','location-navigation');
  const previous=node('button','secondary','←'),next=node('button','secondary','→');previous.type=next.type='button';previous.setAttribute('aria-label','Previous location');next.setAttribute('aria-label','Next location');previous.disabled=overviewUI.boardSite===0;next.disabled=overviewUI.boardSite===sites.length-1;
  previous.hidden=next.hidden=sites.length<2;
  function choose(index){overviewUI.preferredPin=null;overviewUI.boardSite=index;overviewUI.side=preferredBoardSide(sites[index],overviewUI.side);overviewUI.target=0;overviewUI.sheetFocus=null;renderOverviewMap();}
  previous.addEventListener('click',()=>choose(overviewUI.boardSite-1));next.addEventListener('click',()=>choose(overviewUI.boardSite+1));
  const picker=node('select');picker.setAttribute('aria-label','PCB finding location');
  sites.forEach((s,i)=>{const option=node('option','',`${i+1} / ${sites.length} · ${locationLabel(s)}`);option.value=String(i);picker.append(option);});picker.value=String(overviewUI.boardSite);picker.addEventListener('change',()=>choose(Number(picker.value)));nav.append(previous,picker,next);box.append(nav);
  const canvas=node('div','location-canvas-wrap'),svg=svgNode('svg',{class:'location-canvas',role:'img','aria-label':site.basis+': '+locationLabel(site)});
  const [x,y,width,height]=view.bounds;
  svg.append(svgNode('image',{href:`/reports/${encodeURIComponent(app.snapshot.report_id)}/${view.file}`,x,y,width,height,opacity:'.85'}));
  const path=selectedSignalPath();
  if(path)for(const net of path.nets || [])for(const segment of net.geometry || []){
    const a=plotPoint(segment.start,view),b=plotPoint(segment.end,view);
    svg.append(svgNode('line',{x1:a[0],y1:a[1],x2:b[0],y2:b[1],stroke:'#168b9d','stroke-width':segment.width_mm || .12,'stroke-dasharray':segment.layer?.startsWith('In')?'.3 .2':'none',opacity:'.7'}));
  }
  const markers=site.items.map((item,i)=>drawLocation(svg,item,view,i+1,site.context || referenceOnly));
  const badges=markers.map(m=>{const badge=node('span','location-badge',String(m.number));badge.style.background=m.color;badge.setAttribute('aria-hidden','true');canvas.append(badge);return badge;});
  const bounds=locationBounds(site.items,view);
  function positionBadges(b){
    const rect=svg.getBoundingClientRect(),scale=Math.min(rect.width/b[2],rect.height/b[3]),padX=(rect.width-b[2]*scale)/2,padY=(rect.height-b[3]*scale)/2;
    const placed=[];
    markers.forEach((m,i)=>{let px=padX+(m.point[0]-b[0])*scale;const py=padY+(m.point[1]-b[1])*scale;while(placed.some(p=>Math.abs(p[0]-px)<22 && Math.abs(p[1]-py)<22))px+=22;placed.push([px,py]);badges[i].style.left=px+'px';badges[i].style.top=py+'px';badges[i].hidden=px<0 || py<0 || px>rect.width || py>rect.height;});
    canvas._viewBox=b;
  }
  const controls=enableViewport(svg,bounds,view.bounds,positionBadges);
  const sides=box.querySelector('.side-switch');if(sides){controls.append(sides);box.querySelector('.board-heading').remove();}
  canvas.prepend(svg);box.append(controls,canvas);
  requestAnimationFrame(()=>positionBadges(canvas._viewBox || bounds));
  const observer=new ResizeObserver(()=>positionBadges(canvas._viewBox || bounds));observer.observe(canvas);overviewUI.locationObserver?.disconnect();overviewUI.locationObserver=observer;
  const caption=node('p','location-caption',site.context?'Circuit path under review · no confirmed copper correction':referenceOnly?'Measured objects · no copper edit proposed':'Finding location · numbered objects match the list below');box.append(caption);
  const objectTable=node('table','location-object-table');objectTable.setAttribute('aria-label','Highlighted objects');
  const tableHead=node('thead'),headRow=node('tr');for(const label of ['Object','Net','Layer'])headRow.append(node('th','',label));tableHead.append(headRow);
  const tableBody=node('tbody');site.items.forEach((item,i)=>{const row=node('tr');row.append(node('th','',`${i+1} · ${item.label}`),node('td','',item.net || 'Unassigned'),node('td','',(item.layers || []).join(', ') || 'Unknown'));tableBody.append(row);});
  objectTable.append(tableHead,tableBody);box.append(objectTable);
  const details=node('details','location-details');details.append(node('summary','','Coordinates & measurements'));
  const facts=node('div','location-facts');
  site.items.forEach((item,i)=>{
    const row=node('div','location-fact'),badge=node('span','location-badge-static',String(i+1));
    const text=node('div');text.append(node('strong','',item.label),node('small','',`${item.net || 'No assigned net'} · ${(item.layers || []).join(', ') || 'Layer not resolved'} · X ${item.point[0].toFixed(4)} / Y ${item.point[1].toFixed(4)} mm`));
    if(item.kind==='via')text.append(node('small','',`Copper diameter ${(item.radius_mm*2).toFixed(3)} mm · drill ${item.drill_mm.toFixed(3)} mm · ring ${(item.radius_mm-item.drill_mm/2).toFixed(3)} mm`));
    if(item.approximate)text.append(node('small','', 'Reported location only; exact object outline unavailable.'));
    if(item.envelope)text.append(node('small','', 'Outline shows the pad envelope; copper details remain visible underneath.'));
    row.append(badge,text);facts.append(row);
  });
  const messages=node('div','location-measurements');for(const finding of [...new Set(site.findings)])messages.append(node('p','',finding));
  facts.append(messages);
  const copy=node('button','text-link','Copy location for KiCad');copy.type='button';copy.addEventListener('click',async()=>{
    const text=[group.id+' · '+group.title,app.project.selected_files?.pcb || '',...site.items.map(p=>`${p.label} | net ${p.net || '(none)'} | ${(p.layers || []).join(', ')} | X ${p.point[0].toFixed(4)} mm, Y ${p.point[1].toFixed(4)} mm | UUID ${p.uuid || '(unresolved)'}`),...site.findings].join('\n');
    try{await navigator.clipboard.writeText(text);copy.textContent='Location copied';}catch{const output=node('textarea');output.value=text;output.readOnly=true;output.setAttribute('aria-label','Location text to copy');facts.append(output);output.select();}
  });facts.append(copy);details.append(facts);box.append(details);
  const layers=[...new Set([...site.items.flatMap(p=>p.layers || []),...(path?.nets || []).flatMap(n=>(n.geometry || []).map(s=>s.layer).filter(Boolean))])];
  if(layers.some(l=>l.startsWith('In')) || !layers.includes(overviewUI.side==='front'?'F.Cu':'B.Cu'))box.append(node('p','field-help','Selected object geometry is overlaid across its listed layers. The background is the '+overviewUI.side+' copper view.'));
  if(path && layers.some(l=>l!==(overviewUI.side==='front'?'F.Cu':'B.Cu')))box.append(node('p','field-help','The highlighted path can cross layers. Inner-layer tracks are dashed; the background shows only '+overviewUI.side+' copper.'));
}
