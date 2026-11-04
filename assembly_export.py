"""Draft assembly outputs, with native placement provenance and sourcing decisions."""
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
import zipfile

from .archive import read_project_file
from .checks.assembly import population
from .checks.components import reference_key, _passive_value, REFERENCE
from .native import NativeError, find_kicad, _run, _read_output


def placement_snapshot(project, source, executable=None):
    """Export only the exact PCB bytes analyzed. Never modify the user's files."""
    relative = project.selected_files.get('pcb')
    if not relative:
        return {'status':'unavailable', 'message':'Select a PCB to generate placements.'}
    binary = find_kicad(executable)
    if not binary:
        return {'status':'unavailable', 'message':'Install KiCad CLI to generate the CPL.'}
    try:
        content = read_project_file(source, relative)
        digest = hashlib.sha256(content).hexdigest()
        if digest != project.provenance.get('source_hashes',{}).get(relative):
            raise NativeError('The PCB changed since this report. Run a new check before generating placements.')
        with tempfile.TemporaryDirectory(prefix='pcb-placements-') as temp:
            root = Path(temp)
            board, output = root/'board.kicad_pcb', root/'positions.csv'
            board.write_bytes(content)
            version = _run(binary, ['version'], root, timeout=15)
            _run(binary, ['pcb','export','pos','--format','csv','--units','mm','--side','both',
                          '--use-drill-file-origin','--exclude-dnp','--output',str(output),str(board)], root)
            raw = _read_output(output)
        reader = csv.DictReader(io.StringIO(raw))
        if not {'Ref','PosX','PosY','Rot','Side'}.issubset(reader.fieldnames or []):
            raise NativeError('KiCad returned an unsupported placement format.')
        rows, seen = [], set()
        for row in reader:
            ref = row['Ref']
            if ref in seen:
                raise NativeError('Duplicate placement reference: '+ref)
            seen.add(ref)
            x,y,rotation = (float(row[k]) for k in ('PosX','PosY','Rot'))
            if not all(math.isfinite(v) for v in (x,y,rotation)) or row['Side'] not in ('top','bottom'):
                raise NativeError('Invalid coordinates or board side in KiCad placement export.')
            rows.append({'reference':ref, 'x_mm':x, 'y_mm':y, 'rotation':rotation % 360, 'side':row['Side']})
        return {'status':'generated','rows':rows,'pcb_sha256':digest,'source_file':relative,
                'kicad_version':version,'units':'mm','origin':'KiCad drill/place origin',
                'rotation':'KiCad native, counterclockwise degrees, normalized to 0–360',
                'bottom_x':'KiCad default; --bottom-negate-x is not applied'}
    except (ValueError, OSError, UnicodeError) as exc:
        return {'status':'failed','message':str(exc)}


def generate_assembly(project, source=None, executable=None, progress=lambda message: None):
    old = project.assembly_export.get('placement',{})
    pcb_hash = project.provenance.get('source_hashes',{}).get(project.selected_files.get('pcb'))
    if old.get('status') == 'generated' and old.get('pcb_sha256') == pcb_hash:
        placement = old
    elif source:
        progress('Exporting KiCad component positions…')
        placement = placement_snapshot(project, source, executable)
    else:
        placement = {'status':'unavailable','message':'Run a new check to capture placements from this PCB.'}
    identities = {p['reference']:p for p in project.datasheets.get('parts',[])}
    population_states = {p['reference']:p.get('population') for p in project.assembly.get('parts',[])}
    groups, parts, excluded, blockers = {}, [], [], []
    refs_upper = set()
    fitted = set()
    for ref,c in sorted(project.components.items(), key=lambda item:reference_key(item[0])):
        pop = population_states.get(ref) or population(c)
        if not c.pcb or pop != 'fitted':
            excluded.append({'reference':ref,'reason':pop if c.pcb else 'not on PCB'})
            continue
        if len(c.pcb) != 1 or ref.upper() in refs_upper or REFERENCE.fullmatch(ref) is None:
            blockers.append('Ambiguous PCB reference: '+ref)
            continue
        refs_upper.add(ref.upper())
        fitted.add(ref)
        board, part = c.pcb[0], identities.get(ref,{})
        code = part.get('code','') if part.get('status') in ('ready','provisional') else ''
        status = part.get('match','unresolved') if code else 'unresolved'
        note = part.get('message','') or ('Verify package, pinout and ratings before ordering.' if code else 'No LCSC part has been identified.')
        row = {'reference':ref,'value':board.value,'footprint':board.footprint,'code':code,
               'mpn':part.get('mpn',''),'manufacturer':part.get('manufacturer',''),
               'match':status,'review':note,'product_url':part.get('product_url',''),
               'pdf_url':part.get('pdf_url',''), 'pdf_status':part.get('pdf_status','unavailable'),
               'parameters':part.get('parameters',{}), 'stock':part.get('stock'),
               'placement_excluded':'exclude_from_pos_files' in board.attributes}
        parts.append(row)
        # Value and footprint remain visible even when several rows share a supplier code.
        normalized_value = _passive_value(ref,board.value)
        key = (str(normalized_value.normalize()) if normalized_value is not None else board.value,board.footprint,code,status)
        group = groups.setdefault(key, {'value':board.value,'footprint':board.footprint,'code':code,
                                       'mpn':row['mpn'],'references':[],'match':status})
        group['references'].append(ref)
    eligible = {p['reference'] for p in parts if not p['placement_excluded']}
    rows = [r for r in placement.get('rows',[]) if r['reference'] in eligible]
    placed = {r['reference'] for r in rows}
    for p in parts:
        p['placement'] = 'included' if p['reference'] in placed else 'excluded by footprint' if p['placement_excluded'] else 'missing'
    if blockers:
        # Never emit a partial CPL/BOM that silently drops duplicate identities.
        rows = []
    limits = ['Draft part selections require package, pinout and electrical-rating review before ordering.',
              'Inspect origin, bottom-side orientation and each footprint rotation in the assembler preview. No JLCPCB-specific rotation offsets have been applied.',
              'LCSC availability does not establish JLCPCB assembly availability. Stock is not reserved.',
              'DNP, excluded-from-BOM and mechanical components are omitted. Footprints excluded from position files remain in the BOM but have no CPL row.']
    return {'status':'blocked' if blockers else 'draft','bom':list(groups.values()),'parts':parts,
            'placement':placement,'cpl':rows,'excluded':excluded,'blockers':blockers,'limits':limits,
            'counts':{'fitted':len(parts),'bom_lines':len(groups),'placed':len(rows),
                      'assigned':sum(bool(p['code']) for p in parts),
                      'provisional':sum(p['match'] in ('automatic_candidate','value_package_candidate','footprint_candidate') for p in parts),
                      'unresolved':sum(not p['code'] for p in parts),
                      'missing_placements':len(eligible-placed)}}


def csv_bytes(headers, rows):
    # Escape untrusted text; numeric negative coordinates must remain numeric.
    def cell(v):
        if isinstance(v,(int,float)):
            return format(v,'.6f').rstrip('0').rstrip('.') if isinstance(v,float) else v
        text = str(v if v is not None else '')
        return "'"+text if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r','\n')) else text
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(headers)
    writer.writerows([[cell(v) for v in row] for row in rows])
    return out.getvalue().encode('utf-8-sig')


def assembly_files(data):
    export = data.get('assembly_export',{})
    if not export:
        return {}
    files = {}
    if export.get('status') != 'blocked':
        files['bom-draft.csv'] = csv_bytes(['Comment','Designator','Footprint','LCSC Part #'],
            [[b['value'],','.join(b['references']),b['footprint'],b['code']] for b in export['bom']])
        if export.get('placement',{}).get('status') == 'generated':
            files['cpl-draft.csv'] = csv_bytes(['Designator','Mid X','Mid Y','Layer','Rotation'],
                [[r['reference'],r['x_mm'],r['y_mm'],r['side'],r['rotation']] for r in export['cpl']])
    fields = ['reference','value','footprint','code','mpn','manufacturer','match','review','placement','pdf_status','product_url','pdf_url']
    files['sourcing-review.csv'] = csv_bytes(fields+['parameters','stock_snapshot'],
        [[p.get(k,'') for k in fields]+[json.dumps(p.get('parameters',{}),ensure_ascii=False),p.get('stock','')] for p in export['parts']])
    files['assembly-notes.txt'] = ('DRAFT ASSEMBLY FILES — '+data['archive']+'\n\n'
        +'\n'.join(export.get('blockers',[])+export.get('limits',[]))
        +'\n\nCounts: '+json.dumps(export['counts'])
        +'\nPlacement: '+json.dumps({k:v for k,v in export.get('placement',{}).items() if k!='rows'},indent=2)
        +'\n\nBlank BOM codes require an exact identity; automatic candidates are listed in sourcing-review.csv.\n'
        +'Excluded components: '+json.dumps(export.get('excluded',[]))
        +'\n\nFormats: https://jlcpcb.com/help/article/bill-of-materials-for-pcb-assembly\n'
        +'https://jlcpcb.com/help/article/pick-place-file-for-pcb-assembly\n').encode()
    files['assembly-manifest.json'] = json.dumps({'format_version':1,'status':export['status'],
        'source_hashes':data.get('provenance',{}).get('source_hashes',{}),
        'files':{name:hashlib.sha256(content).hexdigest() for name,content in files.items()}},indent=2).encode()
    return files


def assembly_bundle(data):
    out = io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,content in assembly_files(data).items():
            archive.writestr(name,content)
    return out.getvalue()
