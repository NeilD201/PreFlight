"""Retrieve LCSC product identities and their linked PDFs, with bounded caching.

Automatic candidates populate draft assembly files. Only declared LCSC codes,
exact MPN matches and explicit selections become confirmed AI evidence.
"""
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from .checks.assembly import package_code, population
from .checks.components import _passive_value, values_match, reference_key

HOSTS = {'www.lcsc.com', 'lcsc.com', 'datasheet.lcsc.com', 'wmsc.lcsc.com'}
SEARCH = 'https://wmsc.lcsc.com/ftps/wm/search/v3/global'
MAX_PDF = 16 * 1024 * 1024


class DatasheetError(ValueError):
    pass


def safe_url(url):
    try:
        u = urlsplit(url)
        valid = u.scheme == 'https' and u.hostname in HOSTS and not u.username and not u.password and u.port in (None, 443)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise DatasheetError('Only HTTPS links on the supported LCSC hosts can be downloaded.')
    return url


class LCSCRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, limit=4_000_000, payload=None):
    safe_url(url)
    headers = {'User-Agent': 'PCBPreflight/0.3 (component datasheet lookup)', 'Accept': '*/*'}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    try:
        with build_opener(LCSCRedirect()).open(Request(url, data=body, headers=headers), timeout=20) as response:
            safe_url(response.url)
            data = response.read(limit + 1)
        if len(data) > limit:
            raise DatasheetError('LCSC document exceeded the download size limit.')
        return data
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise DatasheetError(f'LCSC returned HTTP {code}. Try again later or enter the exact LCSC code.') from None
    except (URLError, TimeoutError, OSError):
        raise DatasheetError('Could not reach LCSC. Existing checks and cached datasheets are still available.') from None


class ProductHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.current = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('type') == 'application/ld+json':
            self.active, self.current = True, []

    def handle_data(self, data):
        if self.active:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active:
            self.active = False
            try:
                self.documents.append(json.loads(''.join(self.current)))
            except ValueError:
                pass


def product_metadata(html, code):
    parser = ProductHTML()
    parser.feed(html)
    for item in parser.documents:
        if not isinstance(item, dict) or item.get('@type') != 'Product' or item.get('sku') != code:
            continue
        document = item.get('subjectOf', {})
        if isinstance(document, list):
            document = next((d for d in document if d.get('name') == 'Datasheet'), {})
        url = document.get('url', '')
        if not url or '.pdf' not in urlsplit(url).path.lower():
            raise DatasheetError('This LCSC product does not expose a downloadable PDF datasheet.')
        package = next((v.get('value', '') for v in item.get('additionalProperty', [])
                        if v.get('name', '').lower() in ('package', 'packaging')), '')
        return {'code': code, 'mpn': item.get('mpn', ''), 'manufacturer': item.get('brand', {}).get('name', ''),
                'package': package, 'pdf_url': safe_url(url), 'product_url': f'https://www.lcsc.com/product-detail/{code}.html'}
    raise DatasheetError('LCSC did not return the requested product identity. No datasheet was assigned.')


def normalize_mpn(value):
    # Preserve suffix and punctuation: /OT, /MC, +T and TR are not interchangeable.
    return re.sub(r'\s+', '', value).upper()


def candidates_in(value):
    result = {}
    def visit(obj, depth=0):
        if depth > 12:
            return
        if isinstance(obj, dict):
            code, mpn = obj.get('productCode'), obj.get('productModel')
            if isinstance(code, str) and re.fullmatch(r'C[1-9][0-9]{1,11}', code) and isinstance(mpn, str):
                result[code] = {'code': code, 'mpn': mpn[:160], 'manufacturer': str(obj.get('brandNameEn') or '')[:160],
                                'package': str(obj.get('encapStandard') or '')[:160],
                                'description': str(obj.get('productIntroEn') or obj.get('productDescEn') or '')[:1000],
                                'parameters': {str(p.get('paramNameEn'))[:100]:str(p.get('paramValueEn') or '')[:200]
                                               for p in (obj.get('paramVOList') or [])[:40] if isinstance(p, dict)},
                                'stock': obj.get('stockNumber', obj.get('stockSz')),
                                'product_url': f'https://www.lcsc.com/product-detail/{code}.html'}
            else:
                for key, child in obj.items():
                    if key in ('exactMatchResult', 'productSearchResultVO', 'productList', 'result', 'list', 'dataList', 'tipProductDetailUrlVO'):
                        visit(child, depth+1)
        elif isinstance(obj, list):
            for child in obj[:100]:
                visit(child, depth+1)
    visit(value)
    return list(result.values())[:30]


class LCSCClient:
    def __init__(self, cache):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)

    def read_cache(self, name, age=7*86400):
        path = self.cache / name
        try:
            if not path.is_symlink() and time.time() - path.stat().st_mtime < age:
                return json.loads(path.read_text())
        except (OSError, ValueError):
            pass
        return None

    def write_cache(self, name, value):
        path = self.cache / name
        pending = path.with_suffix('.pending')
        pending.write_text(json.dumps(value), encoding='utf-8')
        pending.replace(path)

    def product(self, code):
        if not re.fullmatch(r'C[1-9][0-9]{1,11}', code):
            raise DatasheetError('Enter an LCSC code such as C150772.')
        name = code + '-product.json'
        cached = self.read_cache(name)
        if cached:
            return cached
        metadata = product_metadata(fetch(f'https://www.lcsc.com/product-detail/{code}.html').decode('utf-8'), code)
        self.write_cache(name,metadata)
        return metadata

    def search(self, query):
        query = query.strip()[:120]
        cache_name = 'search-v4-' + hashlib.sha256(query.encode()).hexdigest() + '.json'
        cached = self.read_cache(cache_name, 86400)
        if cached is not None:
            return cached
        def exact(term):
            data = json.loads(fetch(SEARCH, payload={'keyword': term, 'currentPage': 1, 'pageSize': 10}))
            if data.get('code') != 200:
                raise DatasheetError('LCSC search is unavailable; enter a product code to fetch its datasheet.')
            return candidates_in(data)
        requested = query.replace('_', '+')
        base = re.match(r'(?:[A-Za-z]+\d+[A-Za-z]*|\d{8,})', query)
        if not base:
            return []
        family = base.group().upper()
        voltage = re.match(r'([A-Za-z]+\d+[A-Za-z]*-\d+(?:\.\d+)?)', query)
        prefix = voltage.group().upper() if voltage else family
        def compatible(candidate):
            model = candidate['mpn'].upper()
            if re.search(r'(?:-DK|-DONGLE|EVAL|EVALUATION|DEVELOPMENT BOARD)', model + ' ' + candidate.get('description',''), re.I):
                return False
            if not model.startswith(prefix):
                return False
            # A digit after a matched voltage changes its value (1.8 vs 1.85).
            return not voltage or len(model) == len(prefix) or not model[len(prefix)].isdigit()
        found = [c for c in exact(requested) if compatible(c)]
        if not found:
            # A family query may yield categories, so follow at most two public MPN suggestions.
            data = json.loads(fetch('https://wmsc.lcsc.com/ftps/wm/search/v2/pre?keyword=' + quote(requested)))
            suggestions = [v.get('value') for block in data.get('result') or [] if block.get('type') == 'MPN'
                           for v in block.get('resultList', []) if isinstance(v.get('value'), str)]
            for suggestion in [s for s in suggestions if compatible({'mpn':s})][:8]:
                found.extend(c for c in exact(suggestion) if compatible(c))
        found = list({c['code']: c for c in found}.values())[:20]
        self.write_cache(cache_name, found)
        return found

    def passive_search(self, ref, value, footprint):
        kind, package = ref[0], package_code(footprint)
        amount = _passive_value(ref, value)
        if kind not in 'RCL' or amount is None or not package:
            return []
        # Query canonical units so 0.1uF and 100nF share a cache entry.
        unit = {'R':'Ω','C':'F','L':'H'}[kind]
        number = format(amount.normalize(), 'f')
        for factor, prefix in [('1',''),('0.001','m'),('0.000001','u'),('0.000000001','n'),('0.000000000001','p')]:
            from decimal import Decimal
            scaled = amount / Decimal(factor)
            if scaled >= 1 or prefix == 'p':
                number = format(scaled.normalize(), 'f') + prefix
                break
        query = number + unit + ' ' + package
        name = 'passive-v2-' + hashlib.sha256((kind+query).encode()).hexdigest() + '.json'
        cached = self.read_cache(name, 86400)
        if cached is not None:
            return cached
        data = json.loads(fetch(SEARCH, payload={'keyword': query, 'currentPage':1, 'pageSize':25}))
        if data.get('code') != 200:
            raise DatasheetError('LCSC passive search is unavailable. Retry later.')
        candidates = candidates_in(data)
        if not candidates:
            categories = (data.get('result') or {}).get('topResults') or []
            names = {'C':r'ceramic capacitors', 'R':r'chip resistor|thick film resistor|thin film resistor',
                     'L':r'inductors'}
            category = next((c for c in categories if re.search(names[kind], c.get('catalogNameEn',''), re.I)), None)
            if category:
                data = json.loads(fetch('https://wmsc.lcsc.com/ftps/wm/product/query/list', payload={
                    'catalogIdList':[category['catalogId']], 'keyword':'', 'globalKeyword':query,
                    'scene':'FULL_MATCH', 'currentPage':1, 'pageSize':25, 'isStock':True,
                    'paramNameValueMap':{}, 'brandIdList':[], 'encapValueList':[]}))
                if data.get('code') != 200:
                    raise DatasheetError('LCSC category search is unavailable. Retry later.')
                candidates = candidates_in(data)
        field = {'R':'Resistance','C':'Capacitance','L':'Inductance'}[kind]
        found = [c for c in candidates if c['package'] == package and
                 values_match(ref, value, c['parameters'].get(field,''))]
        self.write_cache(name, found[:8])
        return found[:8]

    def document(self, code):
        if not re.fullmatch(r'C[1-9][0-9]{1,11}', code):
            raise DatasheetError('Enter an LCSC code such as C150772.')
        cached = self.read_cache(code + '.json')
        if cached:
            return {**cached, 'cached': True}
        metadata = self.product(code)
        pdf = fetch(metadata['pdf_url'], MAX_PDF)
        if not pdf.startswith(b'%PDF-'):
            raise DatasheetError('The linked document is not a PDF. No text was sent to AI.')
        fingerprint = hashlib.sha256(pdf).hexdigest()
        path = self.cache / (fingerprint + '.pdf')
        path.write_bytes(pdf)
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('pdf_text.py')), str(path)],
                                    capture_output=True, timeout=30, check=False)
            parsed = json.loads(result.stdout)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            raise DatasheetError('PDF extraction failed or exceeded its time limit. Open the original PDF.') from None
        if parsed.get('error'):
            raise DatasheetError(parsed['error'])
        pages = [p for p in parsed['pages'] if p['text'].strip()]
        if sum(len(p['text']) for p in pages) < 100:
            raise DatasheetError('This datasheet has too little readable text, possibly a scan. Automatic review needs a text PDF.')
        value = {**metadata, **parsed, 'sha256': fingerprint, 'retrieved': datetime.now(timezone.utc).isoformat(), 'cached': False}
        self.write_cache(code + '.json', value)
        return value


def part_signature(component):
    records = component.schematic or component.pcb
    identity = [(r.value, r.footprint, sorted(r.properties.items())) for r in records]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def footprint_part_hint(footprint):
    """Use order-code-shaped footprint names, not dimensions or generic connectors."""
    library, _, name = footprint.partition(':')
    if re.fullmatch(r'\d{8,}',name):
        return name
    if library and re.fullmatch(r'[A-Za-z]+-\d+[A-Za-z0-9-]+',library):
        return library
    if library == 'Connector_JST':
        match = re.search(r'JST_[A-Z]+_([A-Z]\d+[A-Z]-[A-Z]+-[A-Z]+)',name)
        if match:
            return match[1]
    return ''


def package_fit(footprint, candidate):
    """Reject recognizable package conflicts; unknown custom names remain provisional."""
    def shape(text):
        text = text.upper().replace('VSSOP', 'SSOP').replace('TSSOP','SSOP').replace('TDFN', 'DFN').replace('AQFN', 'QFN')
        m = re.search(r'(SOT)[-_ ]?(\d+)(?:-(\d+))?', text)
        if m:
            return m.group(1), m.group(2) + ('-' + m.group(3) if m.group(3) else '')
        m = re.search(r'(DFN|QFN|LGA|BGA|WLP|WLCSP|SSOP|SOIC|SOP|SON)[-_ ]?(\d+)', text)
        if m and text[m.end():].startswith('P'):
            return None  # IPC footprint names encode pitch here, not the pin count.
        return (m.group(1), m.group(2)) if m else None
    a, b = shape(footprint), shape(candidate.get('package',''))
    if a and b:
        if a[0] == b[0] == 'SOT' and {a[1],b[1]} <= {'23-5','25','25-5'}:
            return None if a != b else True
        if {a[0],b[0]} == {'BGA','WLP'}:
            return False if a[1] != b[1] else None
        return a == b
    return None


def candidate_choice(ref, value, footprint, candidates, pin_count=None, manufacturer_hint=''):
    # Exact orderable identities win. Family choices stay explicitly provisional.
    def pins_fit(c):
        m = re.search(r'(?:SOT[- ]?\d+[- ]|(?:T?DFN|A?QFN|LGA|BGA|WLP|WLCSP|V?T?SSOP|SOIC|SOP|SON|OESIP)[- ]?)(\d+)', c.get('package',''), re.I)
        return not pin_count or not m or int(m[1]) in (pin_count,pin_count-1)
    usable = [c for c in candidates if package_fit(footprint, c) is not False and pins_fit(c)
              and not re.search(r'DONGLE|EVALUATION|DEVELOPMENT BOARD|(?:^|-)DK(?:$|-)', c['mpn']+' '+c.get('description',''), re.I)]
    exact = [c for c in usable if normalize_mpn(c['mpn']) == normalize_mpn(value)]
    if len(exact) == 1:
        return exact[0], 'exact_mpn'
    if not usable:
        return None, ''
    normalized = normalize_mpn(value.replace('_','+'))
    def rank(c):
        stock = c.get('stock')
        maker = re.sub(r'[^a-z0-9]','',c.get('manufacturer','').lower())
        return (not maker or maker not in manufacturer_hint.lower(),
                normalize_mpn(c['mpn']) != normalized,
                package_fit(footprint, c) is not True,
                not isinstance(stock,(int,float)) or stock <= 0)
    return sorted(usable, key=rank)[0], 'automatic_candidate'


def _fetch_all(client, jobs, progress, label):
    """Run independent LCSC fetches concurrently; jobs is [(key, kind, args)].

    Returns {key: (ok, value_or_exception)}. Exceptions are replayed at the
    original call site so per-reference error handling is unchanged; only the
    wait is shared. Four workers stay polite to the catalog endpoint while
    collapsing ~40s of sequential ~2s lookups into one round.
    """
    from concurrent.futures import ThreadPoolExecutor
    results, done, total = {}, 0, len(jobs)

    def run(job):
        key, kind, args = job
        try:
            if kind == 'passive':
                return key, True, client.passive_search(*args)
            if kind == 'search':
                return key, True, client.search(*args)
            return key, True, client.document(*args)
        except Exception as exc:
            return key, False, exc

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='lcsc-fetch') as pool:
        for key, ok, value in pool.map(run, jobs):
            results[key] = (ok, value)
            done += 1
            if done == total or done % 8 == 0:
                progress(f'LCSC · {label} {done}/{total}…')
    return results


def _replay(results, key):
    """Return a prefetched value, re-raising its stored error at this site."""
    ok, value = results[key]
    if not ok:
        raise value
    return value


def collect_datasheets(project, cache, progress=lambda text: None):
    client, documents, parts = LCSCClient(cache), {}, []
    inventory = {p['reference']: p for p in project.assembly.get('parts', [])}
    identities = project.circuit.get('identities', {})
    overrides = project.design_context.get('part_choices', {})
    failures, started = {}, time.monotonic()
    ordered = sorted(project.components.items(), key=lambda item: (not item[0].startswith('U'), reference_key(item[0])))
    # Pass 1 (local planning, no network): per-reference decisions that need
    # no catalog data, in the same U-first order as before. Distinct search
    # keys are admitted in first-seen order up to the same 80-key cap.
    planned, seen_keys = [], set()
    for ref, component in ordered:
        identity = {**identities.get(ref, {}), **inventory.get(ref, {})}
        records = component.schematic or component.pcb
        value = identity.get('mpn') or identity.get('value') or (records[0].value if records else '')
        footprint = identity.get('footprint') or (records[0].footprint if records else '')
        item = {'reference': ref, 'query': value, 'footprint':footprint, 'signature': part_signature(component),
                'candidates': [], 'status': 'unmatched', 'pdf_status':'unavailable'}
        job = {'ref': ref, 'component': component, 'identity': identity, 'value': value,
               'footprint': footprint, 'item': item, 'code': '', 'action': 'lookup'}
        if identity.get('population', population(component)) != 'fitted':
            job.update(action='emit', item=item | {'status':'excluded', 'message':'Excluded from the fitted BOM by the project population flags.'})
        elif len(component.pcb) > 1:
            job.update(action='emit', item=item | {'status':'identity_conflict', 'message':'Duplicate PCB reference; resolve it in KiCad before assigning a part.'})
        else:
            override = overrides.get(ref, {})
            valid_override = override.get('signature') == part_signature(component)
            code = override.get('code', '') if valid_override else identity.get('lcsc', '')
            passive = bool(re.fullmatch(r'[RCL]\d+', ref) and not identity.get('mpn') and _passive_value(ref,value) is not None)
            search_key = (ref[0] if passive else '', value, footprint)
            job.update(valid_override=valid_override, code=code, passive=passive, search_key=search_key)
            if identity.get('conflicts') and not valid_override:
                job['conflict'] = True
            elif code:
                job['document_key'] = ('document', code)
            elif search_key not in seen_keys and len(seen_keys) >= 80:
                job.update(action='emit', item=item | {'status':'deferred', 'message':'Lookup budget reached. Run again to continue using the cached results.'})
            else:
                seen_keys.add(search_key)
                job['search_key'] = ('passive' if passive else 'search', search_key,
                                     (ref, value, footprint) if passive else (value,))
        planned.append(job)
    # Round A (network): unique catalog searches plus documents for codes the
    # design already declares. One fetch per distinct key, as before.
    searches, needed = {}, []
    for job in planned:
        key = job.get('search_key')
        if key and key[1] not in searches:
            searches[key[1]] = None
            needed.append((key[1], key[0], key[2]))
    pending_codes = []
    for job in planned:
        key = job.get('document_key')
        if key and key[1] not in documents and key[1] not in pending_codes:
            pending_codes.append(key[1])
            needed.append((key, 'document', (key[1],)))
    fetched = _fetch_all(client, needed, progress, 'catalog') if needed else {}
    for key, value in fetched.items():
        if isinstance(key, tuple) and key[0] == 'document':
            ok, payload = value
            if ok:
                documents[key[1]] = payload
            else:
                failures[key[1]] = str(payload)
        else:
            searches[key] = value
    # Pass 2 (local): attach candidates, plan footprint-hint retries for refs
    # whose primary search came back empty.
    hints = []
    for job in planned:
        if job['action'] != 'lookup' or job.get('conflict') or job.get('code'):
            continue
        ref, value, footprint, passive = job['ref'], job['value'], job['footprint'], job['passive']
        item = job['item']
        progress(f'LCSC · {ref}: {value}')
        try:
            item['candidates'] = _replay(searches, job['search_key'][1])
        except (DatasheetError, ValueError, KeyError, TypeError, AttributeError) as exc:
            job.update(action='emit', item=item | {'status':'failed', 'message':str(exc) if isinstance(exc, DatasheetError) else 'LCSC returned an unsupported response. Enter the exact product code or retry later.'})
            continue
        job['query_value'] = value
        if not item['candidates'] and not passive:
            hint = footprint_part_hint(footprint)
            if hint:
                job['query_value'] = hint
                item['query_origin'] = 'footprint'
                hint_key = ('footprint', hint, footprint)
                if hint_key not in searches:
                    searches[hint_key] = None
                    hints.append((hint_key, 'search', (hint,)))
    # Round B (network): footprint-hint retries only.
    hinted = {job['ref'] for job in planned if 'query_origin' in job['item']}
    if hints and time.monotonic() - started <= 240:
        for key, value in _fetch_all(client, hints, progress, 'hints').items():
            searches[key] = value
    elif hints:
        for job in planned:
            if job['ref'] in hinted and job['action'] == 'lookup' and not job.get('code'):
                job.update(action='emit', item=job['item'] | {'status':'deferred', 'message':'Lookup budget reached. Run again to continue using the cached results.'})
    # Pass 3 (local): candidate choice, then prefetch documents for chosen codes.
    pending = []
    for job in planned:
        if job['action'] != 'lookup' or job.get('conflict') or job.get('code'):
            continue
        ref, value, footprint, passive = job['ref'], job['value'], job['footprint'], job['passive']
        identity, component, item = job['identity'], job['component'], job['item']
        if 'query_origin' in item and item['candidates'] == []:
            try:
                hint_key = ('footprint', job['query_value'], footprint)
                item['candidates'] = _replay(searches, hint_key)
            except (DatasheetError, ValueError, KeyError, TypeError, AttributeError) as exc:
                job.update(action='emit', item=item | {'status':'failed', 'message':str(exc) if isinstance(exc, DatasheetError) else 'LCSC returned an unsupported response. Enter the exact product code or retry later.'})
                continue
        pin_count = len({p.number for r in component.pcb for p in r.pads if p.number})
        hint = (identity.get('manufacturer','') + ' ' + (urlsplit(identity.get('datasheet','')).hostname or ''))
        chosen, match = candidate_choice(ref,job['query_value'],footprint,item['candidates'],pin_count,hint)
        if chosen:
            job['code'] = chosen['code']
            item.update(chosen, match=match)
            if passive:
                item['match'] = 'value_package_candidate'
            elif item.get('query_origin') == 'footprint':
                item['match'] = 'footprint_candidate'
            if chosen['code'] not in documents and chosen['code'] not in failures and chosen['code'] not in pending:
                pending.append(chosen['code'])
        else:
            item['message'] = 'No compatible candidate found. Add the exact MPN or LCSC code; generic labels and missing specifications cannot identify a part.'
            if ref.startswith('Y') and 'CRYSTAL' in value.upper():
                item['message'] = 'Crystal frequency and load capacitance are missing. Add those specifications or an exact MPN/LCSC code.'
            elif value.upper() == 'LED':
                item['message'] = 'Specify the LED color, current and voltage, or an exact MPN/LCSC code.'
            elif value.upper() == 'D':
                item['message'] = 'Specify the diode type, reverse-voltage and current ratings, or an exact MPN/LCSC code.'
            job['code'] = ''
    # Round C (network): documents for automatically chosen codes.
    if pending and time.monotonic() - started <= 240:
        for key, value in _fetch_all(client, [(('document', code), 'document', (code,)) for code in pending], progress, 'datasheets').items():
            ok, payload = value
            if ok:
                documents[key[1]] = payload
            else:
                failures[key[1]] = str(payload)
    # Pass 4 (local): attach documents and finalize, replaying stored errors
    # at the same sites as the sequential version.
    for job in planned:
        item = job['item']
        if job['action'] == 'emit':
            parts.append(item)
            continue
        identity, footprint = job['identity'], job['footprint']
        valid_override, passive = job['valid_override'], job['passive']
        ref, value = job['ref'], job['value']
        progress(f'LCSC · {ref}: {job.get("code") or value}')
        try:
            if job.get('conflict'):
                item.update(status='identity_conflict', message='Conflicting part identities. Select the intended LCSC product explicitly.')
                parts.append(item)
                continue
            code = job.get('code', '')
            if not code:
                parts.append(item)
                continue
            if job.get('document_key'):
                item.update(code=code, match='user_selected' if valid_override else 'declared_code')
            provisional = item['match'] in ('automatic_candidate','value_package_candidate','footprint_candidate')
            item.update(code=code, status='provisional' if provisional else 'ready')
            if provisional:
                item['message'] = ('Value and package matched. Verify voltage, tolerance, dielectric or power/current rating before ordering.' if passive else
                                   'Variant selected automatically. Verify the full MPN, footprint and pinout before ordering.')
            # Keep sourcing identities when a PDF cannot be extracted.
            try:
                if code in failures:
                    raise DatasheetError(failures[code])
                if code not in documents:
                    documents[code] = client.document(code)
                doc = documents[code]
                item.update(mpn=doc['mpn'], package=doc['package'], manufacturer=doc['manufacturer'],
                            product_url=doc['product_url'], pdf_url=doc['pdf_url'], pdf_status='read')
                declared_mismatch = identity.get('mpn') and normalize_mpn(identity['mpn']) != normalize_mpn(doc['mpn'])
                if not valid_override and (declared_mismatch and not provisional or package_fit(footprint,doc) is False):
                    item.update(status='identity_conflict', message='LCSC identity or package conflicts with the design. Select the intended part explicitly.')
                    item.pop('code', None)
            except DatasheetError as exc:
                failures[code] = str(exc)
                item.update(pdf_status='failed', pdf_message=str(exc))
                # PDF extraction is independent of a retrieved product identity.
                metadata = client.read_cache(code+'-product.json')
                if metadata:
                    item.update(metadata)
                    mismatch = identity.get('mpn') and normalize_mpn(identity['mpn']) != normalize_mpn(metadata['mpn'])
                    if not valid_override and (mismatch and not provisional or package_fit(footprint,metadata) is False):
                        item.update(status='identity_conflict', message='Retrieved product identity conflicts with the design; select the intended part explicitly.')
                        item.pop('code',None)
                elif not item.get('mpn'):
                    item.update(status='failed', message='Product identity could not be retrieved; retry this lookup.')
                    item.pop('code', None)
        except (DatasheetError, ValueError, KeyError, TypeError, AttributeError) as exc:
            item.update(status='failed', message=str(exc) if isinstance(exc, DatasheetError) else 'LCSC returned an unsupported response. Enter the exact product code or retry later.')
        parts.append(item)
    return {'status': 'completed', 'parts': parts, 'documents': documents,
            'ready': sum(p['pdf_status'] == 'read' for p in parts),
            'assigned': sum(bool(p.get('code')) for p in parts),
            'provisional': sum(p['status'] == 'provisional' for p in parts),
            'limits': ['Automatic candidates populate a draft BOM; missing ratings and package variants require review.',
                       'Text excerpts cover selected pages, not every figure or table. Provisional matches support investigation, not confirmed changes.',
                       'LCSC stock is a search-time snapshot, not a reservation or confirmation of JLCPCB assembly availability.']}


def excerpts(project, *, include_audit=True):
    """Select bounded, numbered PDF passages, preserving a verifiable citation ID."""
    from .evidence_analysis import pin_targets, requires_pin_analysis
    result = []
    targets=[target for group in project.review.get('groups',[]) if requires_pin_analysis(project,group)
             for target in pin_targets(project,group)]
    documents = project.datasheets.get('documents', {})
    ready = project.datasheets.get('parts', [])
    codes = {p['code'] for p in ready if p['status'] in ('ready','provisional') and p.get('code')}
    codes = sorted(codes, key=lambda code: (min((0 if p['reference'].startswith('U') else 1 if p['reference'].startswith('Q') else 2 for p in ready if p.get('code')==code),default=3), code))[:18]
    for code in codes:
        doc = documents.get(code, {})
        refs = [p['reference'] for p in ready if p.get('code') == code and p['status'] in ('ready','provisional')]
        provisional = any(p.get('code') == code and p['status'] == 'provisional' for p in ready)
        relevant = ' '.join(g['summary'] + ' ' + g['next_step'] for g in project.review.get('groups', []) if set(refs) & set(g.get('components', [])))
        words = set(re.findall(r'[a-z]{4,}', relevant.lower())) | {'exposed', 'supply', 'recommended', 'layout', 'pin', 'capacitor', 'pullup', 'pull-up', 'reset', 'enable', 'application', 'operating', 'interface'}
        if any(ref.startswith('Q') for ref in refs):words.update({'source','drain','gate','configuration','pinout','connection','mosfet'})
        # Search the entire cached text for the actual flagged pin names. Generic
        # keyword counts can select power/layout pages while omitting the mode rule.
        names={name.casefold() for target in targets if target['reference'] in refs
               for name in re.findall(r'[A-Za-z][A-Za-z0-9]*',target['name'])}
        focused=bool(names)
        choices = []
        for page in doc.get('pages', []):
            # Whole, contiguous passages retain table context better than disconnected sentences.
            text = page['text']
            offsets = set(range(0, len(text), 1500))
            offsets.update(m.start() for m in re.finditer(r'(?im)^.*(?:pin descriptions?|pin definitions?|pin name function|pin description|pin connections?).*$', text))
            for offset in sorted(offsets):
                end = min(len(text), offset + 1800)
                if end < len(text) and '\n' in text[offset+1200:end]:
                    end = text.rfind('\n', offset+1200, end)
                passage = text[offset:end]
                lower=passage.lower()
                score = min(60,sum(lower.count(word) for word in words))
                hits={name for name in names if re.search(r'(?<![a-z0-9])'+re.escape(name)+r'(?![a-z0-9])',lower)}
                if hits:
                    if re.search(r'pin descriptions?|pin definitions?|pin name function|pin connections?', lower[:250]):
                        score += 350
                    power_names={'vdd','vddio','vout','vin','gnd','vss','nc'}
                    score+=sum(25 if name in power_names else 100 for name in hits)
                    score+=20*sum(term in lower for term in ('address','i2c','i²c','i 2c','ground','vddio','select','power-up','powerup','reset','connect','chip select'))
                    if hits-power_names:
                        score+=200*sum(term in lower for term in ('mode selection','connected to ground','connecting sdo','least significant','less significant','i2c slave address','gpio setup'))
                choices.append((score, page['page'], offset, passage))
        # Avoid spending the budget on overlapping copies of the same paragraph.
        chosen=[]
        # Mode-selection hits must not crowd out electrical limits for a supply
        # pin in the same component. Keep one actual operating table alongside
        # the pin descriptions; absolute maxima alone are not operating limits.
        supply_names={'vdd','vddio','vcc','vin','vout','avdd','dvdd'}
        if names & supply_names:
            electrical=[c for c in choices if re.search(r'electrical characteristics|recommended operating',c[3],re.I)
                        and re.search(r'\b(?:vdd(?:[_ ]?io)?|vcc|vin|supply voltage)\b',c[3],re.I)
                        and re.search(r'\d[.,]\d',c[3])]
            if electrical:
                chosen.append(max(electrical,key=lambda c:(bool(re.search(r'\bmin\b.*\bmax\b',c[3],re.I)),-c[1],-c[2])))
        for choice in sorted(choices,key=lambda v:(-v[0],v[1],v[2])):
            if any(choice[1]==old[1] and abs(choice[2]-old[2])<1500 for old in chosen):continue
            chosen.append(choice)
            if len(chosen)>=(6 if focused else 3):break
        for _, page, offset, text in chosen:
            result.append({'evidence_id': f'D:{code}:p{page}:{offset}', 'references': refs, 'mpn': doc.get('mpn'),
                           'part_match': 'provisional' if provisional else 'assigned',
                           'page': page, 'text': text, 'url': doc['pdf_url'] + f'#page={page}', 'sha256': doc['sha256']})
    if include_audit:
        # A functional review reads additional passages. Retain its exact sources
        # for the issue viewer and chat, without feeding an old audit into a new run.
        seen = {d['evidence_id'] for d in result}
        for doc in project.ai_review.get('circuit_audit', {}).get('documents', []):
            code = doc.get('evidence_id', '').split(':')[1:2]
            original = documents.get(code[0], {}) if code else {}
            refs = {p['reference'] for p in ready if p.get('code') in code and p.get('status') in ('ready', 'provisional')}
            if doc.get('evidence_id') not in seen and original.get('sha256') == doc.get('sha256') and set(doc.get('references', [])) <= refs:
                result.append(doc)
                seen.add(doc['evidence_id'])
    return result
