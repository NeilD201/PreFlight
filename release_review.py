"""Evidence-based manufacturing handoff. A completed check is scoped, never sign-off."""
from collections import Counter
from .verification import evaluate, placement_check, debug_access


def checklist_text(data, report_id):
    review = before_ordering(data)
    labels = {'checked':'CHECKED','attention':'REVIEW','partial':'PARTLY CHECKED','missing':'UNVERIFIED'}
    lines = [data.get('provenance',{}).get('project_name') or data.get('archive','Project'),
             'Saved run: '+report_id, '', review['note']]
    for section, label in review['sections'].items():
        lines.extend(['', '## '+label])
        for row in review['checks']:
            if row['section'] != section:
                continue
            lines.extend([labels[row['status']]+': '+row['title'],row['detail'],row['next_step']])
            lines.extend('  '+e for e in row['evidence'])
            lines.append('')
    return '\n'.join(lines).encode('utf-8')


def before_ordering(data):
    checks = set(data.get('checks_performed', []))
    board = data.get('board_checks', {})
    issues = [i for i in data.get('issues', []) if not i.get('excluded')]
    codes = Counter(i.get('code') for i in issues)
    rows = []
    calculations = evaluate(data)['calculations']

    def add(key, section, title, status, detail, next_step, target, evidence=()):
        rows.append(dict(id=key, section=section, title=title, status=status, detail=detail,
                         next_step=next_step, target=target, evidence=list(evidence)))

    def count(*names):
        return sum(codes[n] for n in names)

    def calculated(section):
        found = [c for c in calculations if c['section'] == section]
        status = ('attention' if any(c['status']=='attention' for c in found) else
                  'checked' if found and all(c['status']=='checked' for c in found) else
                  'partial' if any(c['status']=='checked' for c in found) else 'missing')
        return status, found

    connectivity = data.get('connectivity', {})
    mismatches = count('SCHEMATIC_NET_SPLIT_ON_PCB','PCB_NET_MERGES_SCHEMATIC_NETS',
        'PAD_NET_CONFLICT','PIN_MISSING_FROM_PCB','SCHEMATIC_COMPONENT_MISSING_FROM_PCB',
        'NO_CONNECT_PIN_CONNECTED','NO_CONNECT_ON_CONNECTED_NET')
    compared = connectivity.get('status') == 'compared'
    add('connections','circuit','Schematic and PCB agree',
        'attention' if mismatches else 'checked' if compared else 'missing',
        f'{connectivity.get("pins_compared",0)} pin assignments compared; {mismatches} connection discrepancies.' if compared else 'A complete pin-to-pad comparison is unavailable.',
        'Resolve the listed connection discrepancies.' if mismatches else 'Run a full Update if the source files have changed.', 'connections')
    parity = data.get('native_reports', {}).get('drc', {}).get('schematic_parity', [])
    # Native JSON is omitted from the browser payload; retain the count on the snapshot.
    parity_count = board.get('parity_findings', len([p for p in parity if not p.get('excluded')]))
    add('parity','circuit','Footprints match the schematic',
        'attention' if parity_count else 'checked' if board.get('parity_requested') and 'kicad_drc' in checks else 'missing',
        f'{parity_count} KiCad schematic/PCB parity findings.' if board.get('parity_requested') else 'Full KiCad parity has not run; it needs matching project filenames.',
        'Review parity differences before exporting a production revision.', 'placement')
    local = data.get('circuit', {}).get('schematic_checks', [])
    problems = [c for c in local if c.get('status') in ('conflict','review')]
    ai = data.get('ai_review', {})
    add('part_connections','circuit','Part-specific wiring and circuit paths',
        'attention' if problems else 'partial' if local or ai.get('status') == 'completed' else 'missing',
        f'{len(local)} local connection checks; {len(problems)} need review. AI review: {ai.get("status","not run").replace("_"," ")}.',
        'Open Connections for pin-level conclusions. Parts and operating modes outside those checks remain unverified.', 'connections',
        [f'{c.get("reference","")}: {c.get("check","")} — {c.get("detail","")}' for c in local])
    buses = data.get('circuit', {}).get('bus_candidates', [])
    from .memory_review import review_memory
    memory=review_memory(data)
    if memory['devices']:
        add('memory_interface','signals','Memory interface review',
            'attention' if memory['observations'] else 'partial',
            f'{len(memory["devices"])} memory/controller candidates; {len(memory["observations"])} connection observations. Timing and signal integrity remain unverified.',
            'Open What we check for memory connections and the missing controller-specific requirements.',
            'connections',[item['detail'] for item in memory['observations']])
    add('interfaces','circuit','Bus pull-ups, addresses and logic levels', 'partial' if buses else 'missing',
        f'{len(buses)} candidate I²C lines traced to their external resistors. Timing, voltage compatibility and address collisions are not fully verified.',
        'Set the intended bus speed and supply voltages in project context. Confirm rise time and address settings before release.', 'connections',
        [f'{b["net"]}: {b.get("interpretation", "")}' for b in buses])
    bus_status, bus_results = calculated('interfaces')
    add('bus_timing','circuit','External I²C pull-up calculation',bus_status,
        f'{sum(c["status"]=="checked" for c in bus_results)}/{len(bus_results)} lines within the entered rise-time and sink-current limits.' if bus_results else 'No single-rail external pull-up network with known resistor values was identified.',
        'Enter the electrical limits below. The app calculates rise time and sink current using the saved resistors and their tolerance.', 'connections',
        [c['label']+': '+c['detail'] for c in bus_results])
    power_status, power_results = calculated('power')
    add('power','power','Supply current budget',power_status,
        f'{sum(c["status"]=="checked" for c in power_results)}/{len(power_results)} rails within the entered current budget.' if power_results else 'Supply inputs could not be identified from an exported netlist.',
        'Enter the source capacity and simultaneous load currents below. Voltage range, battery charging limits and startup behavior still need separate review.', 'project',
        [c['label']+': '+c['detail'] for c in power_results])
    thermal_status, _ = calculated('thermal')
    protection_status, _ = calculated('esd_margin')
    add('thermal','power','Heating and component ratings','attention' if thermal_status=='attention' else 'partial' if thermal_status!='missing' else 'missing',
        'Junction-temperature estimates are available below when operating limits are entered. Resistor power, capacitor voltage derating and full assembly heating remain unverified.',
        'Verify these at maximum input, load and ambient temperature; nominal values alone are insufficient.', 'project')
    add('protection','power','External connections and fault protection','attention' if protection_status=='attention' else 'partial' if protection_status!='missing' else 'missing',
        'Positive-pulse protection margin can be calculated below. Reverse power, negative pulses, pulse survival, hot-plug, overcurrent and power sequencing remain unverified.',
        'Review the actual connectors and power sources for the intended environment.', 'project')

    prepared = board.get('status') == 'prepared'
    add('refill','pcb','Copper zones recalculated', 'checked' if prepared else 'missing',
        ('KiCad refilled a temporary copy.' if board.get('zone_fill') == 'refilled' else 'The board has no copper zones to refill.') if prepared else board.get('message','This saved run predates automatic copper refill.'),
        'The original project is unchanged. The checked copy is used for DRC and fabrication files.', 'placement')
    physical = count('KICAD_DRC_SHORTING_ITEMS','KICAD_DRC_UNCONNECTED_ITEMS','KICAD_DRC_TRACKS_CROSSING','KICAD_DRC_ZONES_INTERSECT')
    settings = board.get('rule_settings', {})
    connectivity_rules = {'shorting_items','unconnected_items','tracks_crossing','zones_intersect'}
    disabled = connectivity_rules & set(settings.get('ignored_drc', []))
    add('copper_connectivity','pcb','Routed shorts and unconnected copper',
        'attention' if physical else 'checked' if prepared and 'kicad_drc' in checks and settings.get('status') == 'read' and not disabled else 'missing',
        f'{physical} physical connection findings. '+('Checked after copper refill.' if prepared and 'kicad_drc' in checks else 'A completed check after refill is required.')+(' Disabled rules: '+', '.join(sorted(disabled)) if disabled else ''),
        'Resolve actual shorts and open routes in Placement & PCB.' if physical else 'This verifies saved geometry under enabled rules; it does not measure the manufactured board.', 'placement')
    outline = board.get('outline_closed')
    add('outline','pcb','Closed board outline', 'checked' if prepared and outline else 'attention' if prepared else 'missing',
        f'Closed outline; {board.get("width_mm",0):g} × {board.get("height_mm",0):g} mm.' if prepared and outline else 'A valid closed Edge.Cuts outline has not been confirmed.',
        'Confirm cutouts, dimensions and mounting holes match the mechanical design.', 'placement')
    ignored = settings.get('ignored_drc', []) + settings.get('ignored_erc', [])
    exclusions = settings.get('drc_exclusions', 0) + settings.get('erc_exclusions', 0)
    expanded = board.get('expanded_rule_review', {})
    rerun = expanded.get('status') == 'completed'
    extra = [f'{r["kind"].upper()} {f["rule"].replace("_"," ")}: {f["count"]} findings'
             for r in expanded.get('results', []) for f in r.get('findings', [])]
    add('rule_scope','pcb','Ignored rules and saved exceptions',
        'attention' if exclusions or extra else 'checked' if rerun else 'partial' if ignored else 'checked' if settings.get('status') == 'read' else 'missing',
        (f'{len(ignored)} ignored rules rerun on temporary settings; {len(extra)} rule types returned findings; {exclusions} saved exceptions.' if rerun else f'{len(ignored)} rules ignored; {exclusions} saved exceptions.') if settings.get('status') == 'read' else 'Matching project rule settings were not read.',
        'Review the additional findings below. The saved project settings were preserved. Library, simulation and naming notices do not by themselves imply circuit failure.' if rerun else 'Click Update to run ignored rules on a temporary copy.', 'coverage',
        extra+['DRC: '+r.replace('_',' ') for r in settings.get('ignored_drc', [])]+['ERC: '+r.replace('_',' ') for r in settings.get('ignored_erc', [])])
    m = data.get('manufacturing', {})
    mc = m.get('checks', [])
    unsupported = [c for c in mc if c.get('status') == 'outside']
    options = [c for c in mc if c.get('status') == 'option']
    add('process','pcb','Measured dimensions against manufacturer limits', 'attention' if unsupported or options else 'partial' if any(c.get('status')!='supported' for c in mc) else 'checked' if mc else 'missing',
        f'{len(unsupported)} checks outside the selected process; {len(options)} need a special order option.' if mc else 'Choose a manufacturer and board options to check measured dimensions.',
        'Use the required options and resolve limits outside the selected process.', 'manufacturing', m.get('order_settings', []))
    add('signal_integrity','signals','Trace loss, return paths and RF', 'partial' if data.get('signal_paths', {}).get('paths') else 'missing',
        'Available route lengths and estimated DC resistance are shown in Traces & signals. Noise, impedance, return-current paths and antenna performance are not simulated.',
        'Check these against the actual stackup, loads and interface speed. Validate critical paths by simulation or measurement.', 'traces')
    add('mechanical','mechanical','Enclosure fit, connectors and clearances','missing',
        'Board outline is checked separately. Component heights, mating connectors, enclosure fit and tool access are not verified.',
        'Check the assembled 3D model against the enclosure and connector drawings.', 'placement')
    add('assembly_geometry','mechanical','Pin 1, polarity and solderability','missing',
        'Package rotation conventions, stencil apertures, thermal-pad paste and hand-assembly access need review.',
        'Check orientation and paste in the assembler preview, especially connectors, diodes and exposed-pad parts.', 'assembly')

    assembly = data.get('assembly_export', {})
    counts = assembly.get('counts', {})
    unresolved = counts.get('unresolved',0)
    provisional = counts.get('provisional',0)
    add('parts','assembly','Exact orderable part selections', 'attention' if unresolved else 'partial' if provisional else 'checked' if counts.get('fitted') else 'missing',
        f'{counts.get("assigned",0)}/{counts.get("fitted",0)} fitted parts assigned; {unresolved} unresolved; {provisional} automatic candidates need confirmation.',
        'Resolve unassigned parts and confirm automatic matches, package suffixes and ratings.', 'assembly')
    placement_status, placement_evidence = placement_check(data)
    add('placements','assembly','BOM and placement file consistency',placement_status,
        f'{counts.get("fitted",0)} fitted parts; {counts.get("placed",0)} placement rows; {counts.get("missing_placements",0)} required placements missing.',
        'File consistency is checked automatically. Confirm physical orientation in the assembler’s preview.', 'assembly', placement_evidence)
    add('availability','assembly','Stock and approved substitutions','missing',
        'Distributor matches do not reserve stock or prove the assembler can supply the part.',
        'Confirm live availability and approve any substitutions against package, pinout and ratings.', 'assembly')
    debug_status, debug_evidence = debug_access(data)
    add('test_access','test','SWD programming signal connections',debug_status,
        'SWDIO and SWDCLK are traced from each identified target to connector or test pads, with matching PCB nets.' if debug_status=='checked' else 'A complete direct SWD breakout was not verified; see the traced connections below.',
        'Physical access, ground, target voltage and any required reset signal still need review. Other programming interfaces are outside this check.', 'project',debug_evidence)
    add('bringup','test','Bring-up and product validation','missing',
        'Functional, thermal, EMI/EMC and use-specific tests cannot be concluded from these CAD files.',
        'Prepare a first-power-on procedure and test plan with acceptance limits for the intended product.', 'project')
    fab = data.get('fabrication', {})
    add('outputs','files','Gerber and drill files from this revision',
        'checked' if fab.get('status') == 'draft' else 'attention' if fab.get('status') in ('failed','blocked') else 'missing',
        f'{fab.get("file_count",0)} fabrication files exported from the refilled copy, with a common origin and SHA-256 manifest.' if fab.get('status') == 'draft' else fab.get('message','Run Update to generate fabrication files.'),
        'Inspect the draft package in the manufacturer’s Gerber viewer. Export success does not approve the board.', 'manufacturing')
    add('order_preview','files','Final fabrication and assembly preview','missing',
        'The manufacturer’s interpretation, stackup and assembly orientation have not been approved.',
        'Compare its preview against this revision. Confirm copper order, holes, outline, surface finish and component polarity.', 'manufacturing')
    for kind,section,title in [('thermal','power','Junction-temperature estimate'),
                               ('transient','power','Supply-drop estimate for load steps'),
                               ('esd_margin','power','Positive-pulse protection margin'),
                               ('trace_load','signals','Trace voltage drop and power loss')]:
        status,results=calculated(kind)
        add('model_'+kind,section,title,status,
            f'{sum(r["status"]=="checked" for r in results)}/{len(results)} calculations within entered limits. These are conditional models, not physical tests.',
            'Enter the missing operating limits and their source in What we check → Engineering analysis.',
            'project',[r['label']+': '+r['detail'] for r in results if r['status']!='missing'])
        rows[-1]['calculation_section']=kind
    geometry=data.get('board_checks',{}).get('signal_geometry',{})
    measured = geometry.get('status') in ('measured','partial')
    add('routing_geometry','signals','Parallel tracks and ground-fill coverage','partial' if measured else 'missing',
        f'{geometry.get("straight_tracks",0)} straight tracks screened; {geometry.get("parallel_count",0)} nearby parallel pairs. This measures geometry, not noise or return-current flow.' if measured else geometry.get('message','Routing geometry screening has not run for this revision.'),
        'Open What we check → Engineering analysis for measured pairs, plane gaps and analysis limits. A geometry candidate alone is not a required repair.','traces')
    for row in rows:
        row['requirement'] = ('physical' if row['id'] in ('mechanical','assembly_geometry','bringup','order_preview') else
                              'limits' if row['id'] in ('power','bus_timing') else 'review')
        row['calculation_section'] = row.get('calculation_section') or {'power':'power','bus_timing':'interfaces'}.get(row['id'])
        if row['calculation_section']:row['requirement']='limits'
    summary = Counter(r['status'] for r in rows)
    return {'schema_version':1, 'title':'Before ordering',
            'headline':'Verified results and remaining work',
            'summary':dict(summary), 'checks':rows,
            'sections':{'circuit':'Connections', 'power':'Power & protection', 'pcb':'PCB & fabrication',
                        'signals':'Signals & RF','mechanical':'Mechanical & assembly','assembly':'Parts & population',
                        'test':'Programming & validation','files':'Production files'},
            'note':'Checked means the stated check ran successfully on this saved revision. It does not certify the product or complete the items marked unverified.'}
