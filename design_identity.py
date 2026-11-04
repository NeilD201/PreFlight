"""Compare declared CAD identities with cached documents, separately from purchasing."""
import re

from .datasheets import normalize_mpn, package_fit, part_signature


INSTRUCTIONS = '''Review the SAVED DESIGN, not an assumed assembled physical board.
Use each component's design_identity: it compares saved CAD part fields and the cached
datasheet ordering information. Purchasing status provisional does not, by itself, make
the declared schematic identity unknown. Do not ask the user to inspect chip markings,
confirm a fitted part, read a label or reconfirm an identity already present in the files.
When status is documented, assess connections for declared_mpn using the supplied pin
requirements. If their only qualification is an automatic supplier choice, use conditional
keep decisions, not needs_context questions. Keep any required investigate disposition;
that is a scope limitation, not a request for the user to do the comparison.
When there is a real conflict, name the exact conflicting fields and ask which design
part is intended. Missing document coverage is an analysis gap: explain it and do not
turn it into a physical inspection task. A physical-part inspection is relevant only if
the user explicitly asks about an assembled board, substitutions or assembly verification.
library_aliases lists exact library-safe spellings of an explicit part number (for example
a plus sign represented by an underscore). These are not different sensor models and do
not justify a part-selection question or renaming the footprint. confirmed_user_intent
is a saved user choice for this component; do not ask for it again. It does not override
physical CAD differences, prove package fit, or settle an unrelated operating-mode choice.
Old steps and chat replies may contain obsolete confirmation requests; current identity
evidence takes precedence. Do not repeat those requests. This identity comparison does
not establish electrical correctness, footprint dimensions, authentic purchased parts,
or working hardware. Still compare each pin and its operating requirements.
'''


def design_identity(project, reference):
    component = project.components[reference]
    records = component.schematic + component.pcb
    declarations = []
    for record in records:
        # Standard Value and explicit manufacturer-part fields identify the design.
        # Arbitrary imported properties (e.g. a separate uppercase VALUE) remain
        # visible as metadata, rather than silently overriding the standard field.
        fields = [('Value', record.value)] + [(k, v) for k, v in record.properties.items()
            if re.sub(r'[^a-z]', '', k.casefold()) in
            {'mp', 'mpn', 'manufacturerpartnumber', 'manufacturerspartnumber', 'partnumber'}]
        for name, value in fields:
            if value and str(value).strip():
                declarations.append({'file':record.source_file, 'field':name, 'value':str(value).strip()})
    for row in component.bom:
        if row.get('mpn') and row.get('valid') is not False:
            declarations.append({'file':row.get('source_file','BOM'), 'field':'BOM MPN', 'value':str(row['mpn']).strip()})
    explicit = [d for d in declarations if d['field'] != 'Value']
    chosen = explicit or declarations
    values = {normalize_mpn(d['value']) for d in chosen}
    result = {'status':'unresolved', 'declared_mpn':chosen[0]['value'] if chosen else '',
              'declarations':declarations, 'scope':'Saved design identity only; not a purchased-part or pinout verification.'}
    intent=project.design_context.get('component_intent',{}).get(reference,{})
    if intent.get('signature')==part_signature(component):result['confirmed_user_intent']=intent['value']
    if len(values) != 1:
        result['status'] = 'conflict' if values else 'unresolved'
        return result
    declared = next(iter(values))
    # Disagreeing schematic and PCB values remain explicit conflicts. A generic
    # value alongside a precise MPN can be legitimate and is not auto-approved.
    # An underscore can encode an illegal library-name character. Only accept
    # the exact spelling of the explicit MPN, preserving every suffix character.
    def canonical(value):
        normalized=normalize_mpn(value)
        alias=re.sub(r'[+/ ]','_',declared)
        if explicit and alias!=declared and normalized==alias:
            result.setdefault('library_aliases',[]).append({'value':value,'mpn':result['declared_mpn']})
            return declared
        return normalized
    standard = {canonical(r.value) for r in records if r.value}
    if len(standard) > 1:
        result['status'] = 'conflict'
        return result
    if explicit and standard and standard != {declared}:
        result['value_mpn_difference'] = sorted(standard)
        # Generic values and library-safe spellings can differ from an MPN.
        # Do not claim these identities agree without further evidence.
        return result
    part = next((p for p in project.datasheets.get('parts', []) if p['reference']==reference), {})
    result['purchasing_status'] = part.get('status', 'unresolved')
    if part.get('status') not in ('ready', 'provisional'):
        return result
    doc = project.datasheets.get('documents', {}).get(part.get('code'), {})
    selected = normalize_mpn(doc.get('mpn', ''))
    fits = [package_fit(r.footprint, doc) for r in records if r.footprint]
    result['package_family_check'] = 'conflict' if False in fits else 'consistent' if fits and all(v is True for v in fits) else 'unknown'
    if False in fits:
        result['status'] = 'conflict'
        return result
    def has_code(text, code):
        return bool(code and re.search(r'(?<![A-Z0-9_+./-])'+re.escape(code)+r'(?![A-Z0-9_+./-])', text.upper()))
    # Require a literal code in the manufacturer's ordering section; never strip
    # TR or another suffix globally, or equate a family by substring.
    for page in doc.get('pages', []):
        for match in re.finditer(r'order(?:ing)?\s+(?:code|information|part number)', page['text'], re.I):
            passage = page['text'][match.start():match.start()+700]
            preceding=page['text'][max(0,match.start()-700):match.start()]
            if re.search(r'PART\s+NUMBER',preceding,re.I):passage=preceding+passage
            if has_code(passage, declared) and has_code(passage, selected):
                result.update(status='documented', supplier_mpn=doc.get('mpn'),
                    document={'code':part.get('code'), 'page':page['page'], 'quote':passage,
                              'url':doc.get('pdf_url',''), 'sha256':doc.get('sha256')})
                return result
    return result
