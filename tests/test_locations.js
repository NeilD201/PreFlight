const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({console});
vm.runInContext(fs.readFileSync('web/overview.js','utf8'),context);
vm.runInContext(fs.readFileSync('web/locations.js','utf8'),context);
vm.runInContext(`
  via={uuid:'v',kind:'via',point:[10,20],radius_mm:.125,drill_mm:.15,layers:['F.Cu','In1.Cu','B.Cu'],label:'Via',net:'GND'};
  project={workspace:{board_visual:{item_locations:{v:via},copper_layers:via.layers}},issues:[
    {source:'kicad_drc',message:'Diameter .25 / minimum .5',items:[{uuid:'v'}]},
    {source:'kicad_drc',message:'Drill .15 / minimum .3',items:[{uuid:'v'}]},
    {source:'kicad_drc',message:'Ring .05 / minimum .1',items:[{uuid:'v'}]}]};
  group={id:'R1',issue_ids:['F1','F2','F3'],components:[]};
  sites=boardSites(group,project);
`,context);
assert.equal(vm.runInContext('sites.length',context),1);
assert.equal(vm.runInContext('sites[0].findings.length',context),3);
assert.equal(vm.runInContext("preferredBoardSide(sites[0],'back')",context),'back');
vm.runInContext(`
  pad={uuid:'p',kind:'pad',label:'U1 pad 1',point:[12,20],points:[[11,19],[13,19],[13,21],[11,21]],layers:['B.Cu']};
  project.workspace.board_visual.item_locations.p=pad;
  project.issues=[{source:'kicad_drc',message:'clearance .15 / .2',items:[{uuid:'v'},{uuid:'p'}]}];
  sites=boardSites({...group,issue_ids:['F1']},project);
  front={offset:[30,40],bounds:[0,0,100,100],mirrored:false};back={...front,mirrored:true};
`,context);
assert.equal(vm.runInContext('sites[0].items.length',context),2);
vm.runInContext(`
  rfout1={uuid:'c33',kind:'pad',point:[5,5],points:[[4.8,4.8],[5.2,4.8],[5.2,5.2],[4.8,5.2]],layers:['F.Cu'],label:'C33 pad 1',net:'RF_OUT'};
  rfout2={uuid:'l1',kind:'pad',point:[6,5],points:[[5.8,4.8],[6.2,4.8],[6.2,5.2],[5.8,5.2]],layers:['F.Cu'],label:'L1 pad 1',net:'RF_OUT'};
  copper={uuid:'stray-copper',kind:'graphic',point:[32,-24],points:[[36,-25],[32,-23]],width_mm:.2,layers:['F.Cu'],label:'Copper drawing outside board outline',net:'',outside_outline:true};
  project={workspace:{board_visual:{item_locations:{c33:rfout1,l1:rfout2,'stray-copper':copper}}},
    signal_paths:{paths:[{id:'rf-U13',kind:'rf',nets:[{net:'ANT'},{net:'RF_OUT'}],unnetted_copper_outside_outline:[{uuid:'stray-copper'}]}]}};
  rfSites=boardSites({id:'R2',components:['U13'],issue_ids:[],signal_path_id:'rf-U13'},project);
`,context);
assert.equal(vm.runInContext('rfSites.length',context),2,'Show the RF output and disconnected copper drawing as separate locations');
assert.equal(vm.runInContext('rfSites[0].items.length',context),2);
assert.equal(vm.runInContext('rfSites[1].items[0].uuid',context),'stray-copper');
assert.equal(vm.runInContext('rfSites[1].items[0].outside_outline',context),true);
vm.runInContext(`
  // A previously saved report has no detached-artwork field, but its refreshed
  // board geometry still does. Keep the separate marker available on reload.
  project.signal_paths.paths[0]={id:'rf-U13',kind:'rf',nets:[{net:'ANT'},{net:'RF_OUT'}]};
  rfSites=boardSites({id:'R2',components:['U13'],issue_ids:[],signal_path_id:'rf-U13'},project);
`,context);
assert.equal(vm.runInContext('rfSites.length',context),2,'Recover the detached-copper marker from refreshed board geometry');
assert.equal(vm.runInContext('rfSites[1].items[0].uuid',context),'stray-copper');
vm.runInContext(`
  project={workspace:{board_visual:{item_locations:{v:via,p:pad}}},action_plan:{actions:[{group_id:'R1',manufacturer_check:{locations:[{via_uuid:'v',pad_uuid:'p',sides:['B']}]}}]}};
  vipSites=boardSites(group,project);
`,context);
assert.equal(vm.runInContext('vipSites.length',context),1);
assert.equal(vm.runInContext('vipSites[0].basis',context),'Via-in-pad geometry');
assert.equal(vm.runInContext('vipSites[0].items[1].uuid',context),'p');
assert.equal(vm.runInContext("preferredBoardSide(vipSites[0],'front')",context),'back');
assert.deepEqual(Array.from(vm.runInContext('plotPoint([12,20],back)',context)),[18,60]);
for(const side of ['front','back']){
  const b=Array.from(vm.runInContext(`locationBounds(sites[0].items,${side})`,context));
  const pts=vm.runInContext(`sites[0].items.flatMap(locationPoints).map(p=>plotPoint(p,${side}))`,context);
  assert(pts.every(p=>p[0]>b[0] && p[0]<b[0]+b[2] && p[1]>b[1] && p[1]<b[1]+b[3]));
}
vm.runInContext(`
  group={id:'R1',components:['U1'],issue_ids:['F1']};
  visual={pins:{U1:{'2':{kind:'pin',reference:'U1',pin:'2',point:[20,30],label:'U1 pin 2'}}},item_locations:{pin:{kind:'pin',reference:'U1',pin:'2',point:[20,30],label:'U1 pin 2'}}};
  project={issues:[{source:'kicad_erc',items:[{uuid:'pin'}]}],action_plan:{actions:[{group_id:'R1',pin_checks:[{reference:'U1',pin:'2',decision:'change'}]}]}};
  pins=schematicTargets(group,visual,project);
`,context);
assert.equal(vm.runInContext('pins.length',context),1);
assert.equal(vm.runInContext('pins[0].decision',context),'change');
assert(vm.runInContext("pins[0].label.includes('change this connection')",context));
vm.runInContext(`project.issues[0].excluded=true; project.issues[0].source='kicad_drc';empty=boardSites(group,project);`,context);
assert.equal(vm.runInContext('empty.length',context),0);
console.log('Finding pairs, via deduplication, mirrored geometry, focus bounds and exact pin selection: passed');
