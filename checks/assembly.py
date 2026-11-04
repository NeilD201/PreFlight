"""Cross-check assembly files without guessing placement coordinate conventions."""

from collections import defaultdict
import re

from .components import reference_key, values_match
from ..models import Component, Issue


def population(component):
    records = component.schematic + component.pcb
    if any(r.dnp for r in records):
        return 'dnp'
    if any(not r.in_bom for r in records):
        return 'excluded'
    if any(r.board_only and not any(p.number for p in r.pads) for r in component.pcb):
        return 'mechanical'
    if component.pcb and all(re.search(r'(?:^|[:_])(?:MountingHole|Fiducial|Logo)', r.footprint, re.I) for r in component.pcb):
        return 'mechanical'
    return 'fitted'


def package_code(value):
    """Only compare unambiguous imperial passive package codes, not fuzzy IC names."""
    if re.fullmatch(r'(?:0201|0402|0603|0805|1206|1210|1812|2010|2512)', value.strip()):
        return value.strip()
    match = re.search(r'(?:^|:)[RCL]_(0201|0402|0603|0805|1206|1210|1812|2010|2512)_', value)
    return match[1] if match else None


def check_assembly(project, parsed, *, pcb_ready):
    assembly = project.assembly
    assembly.update({'bom_count':0, 'cpl_count':0, 'compared':False,
        'limits':['Placement origin, mirroring, rotation conventions and physical centroid accuracy are not verified.',
                  'Part numbers are declared identities. Supplier stock, price, pinout and ratings have not been verified.']})
    for kind,result in parsed.items():
        project.issues.extend(result['issues'])
        assembly[kind+'_count'] = len({r['reference'] for r in result['records']})
        assembly[kind] = {k:v for k,v in result.items() if k not in ('records','issues')}
        for record in result['records']:
            component = project.components.setdefault(record['reference'], Component(record['reference']))
            getattr(component,kind).append(record)
        project.checks_performed.append(f'{kind}_csv_validation')
    def add(code, ref, message, evidence, severity='warning', fix=None):
        project.issues.append(Issue(severity,'ASSEMBLY_'+code,message,evidence,source='assembly',component=ref,
            suggested_fix=fix or 'Confirm the intended assembly population and regenerate the BOM and placement files from this PCB revision.'))
    ambiguous = set()
    for ref,c in project.components.items():
        for kind in parsed:
            records = getattr(c,kind)
            if len(records)>1:
                ambiguous.add(ref)
                add('DUPLICATE_REFERENCE',ref,f'{ref} occurs more than once in the {kind.upper()} file.',
                    [f'{r["source_file"]}:{r["line"]}' for r in records], 'error')
        if len(c.pcb)>1:
            ambiguous.add(ref)
    if parsed and pcb_ready:
        assembly['compared'] = True
        project.checks_performed.append('assembly_pcb_reference_comparison')
    if 'bom' in parsed and 'cpl' in parsed:
        project.checks_performed.append('bom_cpl_reference_comparison')
    unknown_units = []
    for ref,c in project.components.items():
        if ref in ambiguous:
            continue
        b = c.bom[0] if c.bom else None
        p = c.cpl[0] if c.cpl else None
        board = c.pcb[0] if c.pcb else None
        evidence = [f'{r["source_file"]}:{r["line"]}' for r in c.bom+c.cpl] + [r.location for r in c.pcb]
        state = population(c)
        if b and not b['valid'] or p and not p['valid']:
            continue
        if pcb_ready and not board and (b or p):
            add('REFERENCE_NOT_ON_PCB',ref,f'{ref} is listed in assembly files but is absent from the selected PCB.',evidence,'error')
            continue
        if board and state in ('dnp','excluded','mechanical'):
            if p or b and not b['dnp']:
                add('EXCLUDED_PART_LISTED',ref,f'{ref} is {state} in KiCad but appears in the assembly population.',evidence)
            continue
        if b and b['dnp']:
            if p:
                add('DNP_IN_PLACEMENT',ref,f'{ref} is marked DNP in the BOM but is listed for placement.',evidence)
            continue
        if board and pcb_ready and 'bom' in parsed and not b:
            add('PCB_MISSING_FROM_BOM',ref,f'{ref} is a fitted PCB component missing from the BOM.',evidence)
        placement_excluded = board and ('exclude_from_pos_files' in board.attributes or 'through_hole' in board.attributes)
        if 'cpl' in parsed and not p and (b or board and pcb_ready) and not placement_excluded:
            add('MISSING_PLACEMENT',ref,f'{ref} has no placement row. Confirm whether it is hand assembled or missing from the export.',evidence)
        if p and 'bom' in parsed and not b:
            add('PLACEMENT_MISSING_FROM_BOM',ref,f'{ref} is placed but has no BOM entry.',evidence)
        if b and board:
            if b['value'] and not values_match(ref,b['value'],board.value):
                add('VALUE_DIFFERENCE',ref,f'{ref}: BOM value {b["value"]!r} differs from PCB value {board.value!r}; descriptive comments may contain extra specifications.',evidence)
            left,right = package_code(b['footprint']),package_code(board.footprint)
            if left and right and left != right:
                add('PACKAGE_MISMATCH',ref,f'{ref}: BOM specifies {left}, while the PCB footprint is {right}.',evidence,'error')
            elif ':' in b['footprint'] and b['footprint'] != board.footprint:
                add('FOOTPRINT_DIFFERENCE',ref,f'{ref}: BOM and PCB use different footprint names.',evidence)
        if p:
            if p.get('x_mm') is None or p.get('y_mm') is None:
                unknown_units.append(ref)
            if board and board.layer in ('F.Cu','B.Cu') and p['layer'] != {'F.Cu':'top','B.Cu':'bottom'}[board.layer]:
                add('LAYER_MISMATCH',ref,f'{ref}: placement says {p["layer"]}, but the PCB footprint is on {board.layer}.',evidence,'error')
    if unknown_units:
        add('UNITS_NOT_CONFIRMED',None,f'Placement units are unspecified for {len(unknown_units)} references. Select mm or inches, or include units in the coordinate headers/cells.',[], 'info')
    assembly['unknown_unit_references'] = unknown_units
    missing = [kind.upper() for kind in ('bom','cpl') if kind not in parsed]
    if missing:
        project.issues.append(Issue('info','ASSEMBLY_NOT_PERFORMED',
            f'{" and ".join(missing)} not parsed. Complete BOM/placement comparison is unavailable.',
            source='assembly',suggested_fix='Include one BOM CSV and one placement CSV in the project ZIP, or choose the intended files in check options.'))
    assembly['status'] = 'compared' if not missing and pcb_ready and all(r['status']=='parsed' for r in parsed.values()) else 'partial' if parsed else 'unavailable'
    project.components = dict(sorted(project.components.items(),key=lambda item:reference_key(item[0])))


def parts_inventory(project):
    result = []
    for ref,c in project.components.items():
        if not c.pcb:
            continue
        raw = defaultdict(set)
        aliases = {'manufacturerpartnumber':'mpn','mpn':'mpn','mfrpartnumber':'mpn','manufacturernumber':'mpn',
                   'manufacturer':'manufacturer','mfr':'manufacturer','lcsc':'lcsc','lcscpart':'lcsc','lcscpartnumber':'lcsc'}
        for record in c.schematic+c.pcb:
            for key,value in record.properties.items():
                field = aliases.get(re.sub(r'[^a-z0-9]','',key.lower()))
                if field and value.strip() and value.strip() != '~': raw[field].add(value.strip())
        for record in c.bom:
            if record.get('valid'):
                for field in ('mpn','manufacturer','lcsc'):
                    if record.get(field): raw[field].add(record[field])
        conflicts = [key for key,values in raw.items() if len(values)>1]
        identity = {key:next(iter(values)) if len(values)==1 else '' for key,values in raw.items()}
        pop = population(c)
        if len(c.bom)==1 and c.bom[0].get('dnp'): pop='dnp'
        status = 'conflicting_identity' if conflicts else 'part_number_declared' if identity.get('mpn') or identity.get('lcsc') else 'needs_part_number'
        result.append({'reference':ref, 'value':c.pcb[0].value,'footprint':c.pcb[0].footprint,
                       'population':pop,'status':status,'conflicts':conflicts,**identity})
    return result
