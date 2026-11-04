"""Bounded BOM and placement CSV parsing, with source rows and explicit units."""

import csv
import io
import math
import re

from ..models import Issue

MAX_ROWS = 20000
MAX_REFERENCES = 50000
MAX_CSV_BYTES = 8 * 1024 * 1024


def normalize(value):
    return re.sub(r'[^a-z0-9]', '', value.casefold())


ALIASES = {
    'reference': {'reference', 'references', 'ref', 'refs', 'refdes', 'designator', 'designators'},
    'value': {'value', 'val', 'comment', 'description'},
    'footprint': {'footprint', 'package', 'landpattern'},
    'mpn': {'mpn', 'manufacturerpartnumber', 'manufacturernumber', 'mfrpartnumber', 'mfrno'},
    'manufacturer': {'manufacturer', 'mfr', 'brand'},
    'lcsc': {'lcsc', 'lcscpart', 'lcscpartnumber', 'lcscpartno', 'jlcpcbpartnumber', 'supplierpart'},
    'quantity': {'quantity', 'qty', 'count'},
    'dnp': {'dnp', 'donotpopulate', 'donotplace', 'dnf', 'donotfit'},
    'fitted': {'fitted', 'populate', 'populated', 'assemble'},
    'x': {'x', 'posx', 'midx', 'centerx', 'xmm', 'posxmm', 'midxmm', 'centerxmm', 'xin', 'posxin', 'midxin'},
    'y': {'y', 'posy', 'midy', 'centery', 'ymm', 'posymm', 'midymm', 'centerymm', 'yin', 'posyin', 'midyin'},
    'rotation': {'rotation', 'rot', 'angle', 'rotationdeg', 'rotationdegrees'},
    'layer': {'layer', 'side', 'boardside'},
}


class CSVError(ValueError):
    pass


def references(value):
    """Expand grouped designators and bounded same-prefix ranges; never discard junk."""
    if not value.strip():
        raise CSVError('Missing reference designator.')
    result = []
    for token in re.split(r'[,;\s]+', value.strip()):
        if not token:
            continue
        match = re.fullmatch(r'([A-Za-z]+)(\d+)-([A-Za-z]+)(\d+)', token)
        if match:
            prefix, start, other, end = match.groups()
            if prefix.upper() != other.upper() or not 0 <= int(end)-int(start) <= 1000:
                raise CSVError(f'Unsupported reference range: {token!r}.')
            result.extend(f'{prefix.upper()}{i}' for i in range(int(start), int(end)+1))
        elif re.fullmatch(r'[A-Za-z]+\d+', token):
            result.append(token.upper())
        else:
            raise CSVError(f'Malformed reference: {token!r}.')
        if len(result) > 2000:
            raise CSVError('Too many references in one row.')
    return result


def _number(value):
    # Decimal comma is accepted when quoted or separated by semicolons/tabs.
    text = value.strip().replace(',', '.')
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', text):
        raise ValueError('not a number')
    number = float(text)
    if not math.isfinite(number) or abs(number) > 1e8:
        raise ValueError('non-finite or out of range')
    return number


def coordinate(value, header, configured='auto'):
    text = value.strip()
    match = re.fullmatch(r'(.*?)\s*(mm|mil|inch|inches|in)?', text, re.I)
    number, suffix = match.groups()
    header_unit = ('mm' if normalize(header).endswith('mm') else
                   'in' if normalize(header).endswith('in') else None)
    suffix = {'inch':'in', 'inches':'in'}.get((suffix or '').lower(), (suffix or '').lower()) or None
    explicit = configured if configured != 'auto' else None
    declared = [u for u in (suffix, header_unit, explicit) if u]
    if len(set(declared)) > 1:
        raise ValueError('conflicting coordinate units')
    unit = next(iter(declared), None)
    number = _number(number)
    return number, unit, number * {'mm':1, 'in':25.4, 'mil':.0254}[unit] if unit else None


def parse_assembly(data, source, kind, *, units='auto'):
    if kind not in ('bom', 'cpl') or units not in ('auto', 'mm', 'in'):
        raise CSVError('Unsupported CSV type or coordinate units.')
    if len(data) > MAX_CSV_BYTES:
        raise CSVError('Assembly CSV exceeds 8 MiB.')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeError as exc:
        raise CSVError('Save the CSV with UTF-8 encoding.') from exc
    if '\x00' in text:
        raise CSVError('NUL characters are not supported in CSV files.')
    try:
        dialect = csv.Sniffer().sniff(text[:65536], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text, newline=''), dialect, strict=True)
    try:
        headers = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise CSVError('CSV is empty or has no readable header.') from exc
    normalized = [normalize(h) for h in headers]
    if not headers or len(headers) > 100 or any(not h for h in normalized) or len(set(normalized)) != len(headers):
        raise CSVError('CSV headers must be nonempty, unique, and limited to 100 columns.')
    columns = {}
    for field, aliases in ALIASES.items():
        matches = [i for i,h in enumerate(normalized) if h in aliases]
        if len(matches) > 1:
            # Value and description often coexist; exact Value/Comment wins.
            if field == 'value':
                matches = [min(matches, key=lambda i: ['value','val','comment','description'].index(normalized[i]))]
            else:
                raise CSVError(f'Multiple columns map to {field}; keep one unambiguous column.')
        if matches:
            columns[field] = matches[0]
    if 'reference' not in columns:
        raise CSVError('Missing Reference / Designator column.')
    issues, records = [], []
    def issue(code, message, line, ref=None):
        issues.append(Issue('error', f'ASSEMBLY_{code}', message, [f'{source}:{line}'],
                            component=ref, source='assembly', suggested_fix='Correct the CSV and re-export from the same board revision.'))
    if kind == 'cpl':
        for field in ('x', 'y', 'rotation', 'layer'):
            if field not in columns:
                issue('MISSING_COLUMN', f'Placement CSV is missing its {field} column.', 1)
    try:
        for index, row in enumerate(reader, 1):
            if index > MAX_ROWS:
                raise CSVError(f'CSV exceeds {MAX_ROWS} rows.')
            if not any(c.strip() for c in row):
                continue
            line = reader.line_num
            if len(row) != len(headers):
                issue('ROW_WIDTH', f'CSV row has {len(row)} cells; expected {len(headers)}. Quote grouped references containing commas.', line)
                continue
            values = {field: row[i].strip() for field,i in columns.items()}
            try:
                refs = references(values['reference'])
                if kind == 'cpl' and len(refs) != 1:
                    raise CSVError('Each placement row must contain exactly one reference.')
            except CSVError as exc:
                issue('INVALID_REFERENCE', str(exc), line)
                continue
            base = {k:values.get(k,'') for k in ('value','footprint','mpn','manufacturer','lcsc')}
            base.update(source_file=source, line=line, dnp=False, valid=True)
            for flag in ('dnp', 'fitted'):
                value = values.get(flag, '').casefold()
                if not value:
                    continue
                yes, no = {'yes','true','1','y','dnp','dnf'}, {'no','false','0','n'}
                if value not in yes | no:
                    issue('INVALID_POPULATION', f'Unrecognized {flag} value {value!r}. Use yes/no.', line)
                    base['valid'] = False
                elif (flag == 'dnp' and value in yes) or (flag == 'fitted' and value in no):
                    base['dnp'] = True
            if kind == 'bom' and values.get('quantity'):
                if not values['quantity'].isdigit() or int(values['quantity']) not in ({0,len(refs)} if base['dnp'] else {len(refs)}):
                    issue('QUANTITY_MISMATCH', f'Quantity {values["quantity"]!r} differs from {len(refs)} listed references.', line)
                    base['valid'] = False
            if kind == 'cpl':
                ref = refs[0]
                for field in ('x','y'):
                    base[field+'_mm'] = None
                    try:
                        number,unit,mm = coordinate(values.get(field,''), headers[columns[field]] if field in columns else '', units)
                        base.update({field:number, field+'_unit':unit, field+'_mm':mm})
                    except ValueError:
                        issue('INVALID_COORDINATE', f'{ref} has a missing, invalid, or conflicting-unit {field.upper()} coordinate: {values.get(field, "")!r}.', line, ref)
                        base['valid'] = False
                try:
                    base['rotation'] = _number(values.get('rotation','').removesuffix('°')) % 360
                except ValueError:
                    issue('INVALID_ROTATION', f'{ref} has a missing or invalid rotation.', line, ref)
                    base['rotation'] = None; base['valid'] = False
                layer = normalize(values.get('layer',''))
                base['layer'] = {'top':'top','front':'top','fcu':'top','t':'top',
                                 'bottom':'bottom','back':'bottom','bcu':'bottom','b':'bottom'}.get(layer)
                if not base['layer']:
                    issue('INVALID_LAYER', f'{ref} has an unknown placement layer: {values.get("layer", "")!r}.', line, ref)
                    base['valid'] = False
            for ref in refs:
                records.append(dict(base, reference=ref))
                if len(records) > MAX_REFERENCES:
                    raise CSVError(f'CSV exceeds {MAX_REFERENCES} component references.')
    except csv.Error as exc:
        raise CSVError(f'Malformed CSV near line {reader.line_num}: {exc}') from exc
    if not records:
        issue('EMPTY_FILE', 'No component records could be read from this CSV.', 1)
    return {'records':records, 'issues':issues, 'columns':{k:headers[i] for k,i in columns.items()},
            'status':'partial' if issues else 'parsed', 'row_count':reader.line_num-1}
