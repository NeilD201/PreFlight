"""Evidence cards and real PDF crops. Never fabricate an excerpt or screenshot."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .datasheets import excerpts, MAX_PDF
from .checks.circuit import MCP_SOURCE,MAX_SOURCE,part_rule

MCP=MCP_SOURCE['url'].split('#')[0]
MAX=MAX_SOURCE['url'].split('#')[0]
OFFICIAL={MCP,MAX,'https://www.analog.com/media/en/technical-documentation/data-sheets/max30001.pdf'}
KNOWN={
 'mcp73831_dfn':(MCP,11,'An internal electrical connection exists between the Exposed Thermal Pad (EP) and the VSS pin. They must be connected to the same potential on the Printed Circuit Board (PCB).'),
 'max30102':(MAX,8,'No Connection. Connect to PCB pad for mechanical stability.'),
 'schematic:vdd_capacitor':(MCP,11,'A supply voltage of [VREG (typical) + 0.3V] to 6V is recommended. Bypass to VSS with a minimum of 4.7'),
 'schematic:vbat_capacitor':(MCP,11,'Connect to positive terminal of battery. Drain terminal of internal P-channel MOSFET pass transistor. Bypass to VSS with a minimum of 4.7'),
 'schematic:vled_capacitor':(MAX,8,'LED Power Supply (anode connection). Use a bypass capacitor to PGND for best performance.'),
 'schematic:sda_pullup':(MAX,28,'SDA operates as both an input and an open-drain output.'),
 'schematic:charge_resistor':(MCP,11,'Preconditioning, fast charge and termination currents are scaled by placing a resistor from PROG to VSS.'),
}

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def official_document(url,cache):
    if url not in OFFICIAL:raise ValueError('This source needs manual review; no supported PDF download is available.')
    path=cache/('source-'+hashlib.sha256(url.encode()).hexdigest()+'.pdf')
    if not path.exists():
        with build_opener(NoRedirect()).open(Request(url,headers={'User-Agent':'PCBPreflight/0.6'}),timeout=20) as response:
            data=response.read(MAX_PDF+1)
        if len(data)>MAX_PDF or not data.startswith(b'%PDF-'):raise ValueError('The source did not return a supported PDF.')
        path.write_bytes(data)
    return path


def render_crop(path,page,quote,cache):
    if path.is_symlink() or path.stat().st_size>MAX_PDF:raise ValueError('The PDF is unavailable.')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    key=hashlib.sha256(json.dumps([digest,page,quote,'v5']).encode()).hexdigest()
    image=cache/(key+'.png');metadata=cache/(key+'.json')
    if image.exists() and metadata.exists():return json.loads(metadata.read_text())
    with tempfile.TemporaryDirectory(prefix='proof-',dir=cache) as temp:
        output=Path(temp)/'crop.png'
        request={'path':str(path),'page':page,'quote':quote,'output':str(output)}
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('pdf_proof_render.py'))],input=json.dumps(request),text=True,capture_output=True,timeout=30)
        data=json.loads(result.stdout)
        if data.get('error') or not output.is_file():raise ValueError(data.get('error','Could not render this datasheet.'))
        output.replace(image)
    data.update(image='/proof-images/'+image.name,sha256=digest)
    metadata.write_text(json.dumps(data))
    return data


def issue_proof(project,group,root):
    root=Path(root);cache=root/'.proofs';cache.mkdir(exist_ok=True)
    actions={a['group_id']:a for a in project.action_plan.get('actions',[])}
    action=actions.get(group['id'],{})
    documents={d['evidence_id']:d for d in excerpts(project)}
    candidates=[];notices=[]
    citations=list(action.get('citations',[]) or group.get('citations',[]))
    for check in action.get('pin_checks',[]):citations.extend(check.get('citations',[]))
    seen=set()
    for citation in citations:
        key=(citation.get('evidence_id'),citation.get('quote'))
        if key in seen:continue
        seen.add(key)
        doc=documents.get(citation['evidence_id'])
        # Locate the original document even if later excerpt ranking changed.
        match=re.fullmatch(r'D:(C\d+):p(\d+):(\d+)',citation.get('evidence_id',''))
        if not doc and match:
            original=project.datasheets.get('documents',{}).get(match[1],{})
            if original:doc={**original,'page':int(match[2]),'url':original['pdf_url']+'#page='+match[2]}
        if not doc:continue
        digest=doc.get('sha256','')
        if not re.fullmatch(r'[0-9a-f]{64}',digest):continue
        path=root/'.datasheets'/(digest+'.pdf')
        candidates.append((path,doc['page'],citation['quote'],doc['url'],doc.get('mpn','Datasheet'),digest))
    rule=group.get('rule')
    known=KNOWN.get(rule)
    # Generic capacitor rule names are shared between parts; don't cite the charger for a sensor.
    if rule=='schematic:vdd_capacitor' and any('MAX30102' in r.value.upper() for ref in group.get('components',[]) for r in project.components[ref].schematic):
        known=(MAX,8,'Analog Power Supply Input. Use a bypass capacitor to GND for best performance.')
    # Reuse the fetched part's PDF when it contains this exact requirement.
    # Manufacturer hosts may be unavailable even though the report has a real,
    # hash-checked copy from LCSC. Never show another component's cached PDF.
    if known and not candidates:
        from .pdf_proof_render import normalized
        needle=normalized(known[2])[0]
        for part in project.datasheets.get('parts',[]):
            if part.get('reference') not in group.get('components',[]) or part.get('status') not in ('ready','provisional'):continue
            doc=project.datasheets.get('documents',{}).get(part.get('code'),{})
            family=doc.get('mpn','').upper()
            if not (('MAX30102' in family) if known[0]==MAX else ('MCP73831' in family or 'MCP73832' in family)):continue
            digest=doc.get('sha256','')
            if not re.fullmatch(r'[0-9a-f]{64}',digest):continue
            for page in doc.get('pages',[]):
                if needle not in normalized(page.get('text',''))[0]:continue
                path=root/'.datasheets'/(digest+'.pdf')
                if path.is_file():candidates.append((path,page['page'],known[2],doc['pdf_url']+'#page='+str(page['page']),doc.get('mpn','Datasheet'),digest))
                break
            if candidates:break
    if known and not candidates:
        url,page,quote=known
        try:candidates.append((official_document(url,cache),page,quote,url+'#page='+str(page),'Manufacturer datasheet',None))
        except Exception:notices.append('The datasheet image could not be loaded. Open the source link to review it.')
    if not candidates and not known:
        for source in group.get('sources',[]):
            url=source.get('url','');base=url.split('#')[0]
            page=re.search(r'(?:#|&)page=(\d+)',url)
            if base in OFFICIAL and page:
                try:candidates.append((official_document(base,cache),int(page[1]),'',url,source.get('title','Datasheet'),None))
                except Exception:notices.append('The source PDF could not be loaded.')
    pictures=[]
    for path,page,quote,url,title,expected in candidates[:8]:
        try:
            if expected and hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('Cached PDF does not match the cited document.')
            rendered=render_crop(path,int(page),quote,cache)
            pictures.append({**rendered,'quote':quote,'url':url,'title':title})
        except (OSError,ValueError,subprocess.TimeoutExpired):notices.append('The cited PDF image is unavailable. The source link and original finding remain available.')
    identity=[]
    for ref in group.get('components',[]):
        component=project.components.get(ref)
        if not component:continue
        records=component.schematic+component.pcb
        values=sorted({r.value for r in records if r.value})
        footprints=sorted({r.footprint for r in records if r.footprint})
        rule_match=part_rule(component)
        if rule_match:
            identity.append(f"{ref}: automatically matched the declared part ({', '.join(values)}) to the {'MCP73831 DFN' if rule_match=='mcp73831_dfn' else 'MAX30102'} datasheet family. Footprint: {', '.join(footprints) or 'not recorded'}. This identifies the saved design; it does not verify a purchased component.")
        elif len(values)>1:
            identity.append(f"{ref}: conflicting part values in the files: {', '.join(values)}. An exact part match is unresolved.")
    issues=[]
    for fid in group.get('issue_ids',[]):
        if re.fullmatch(r'F\d+',fid) and 0<int(fid[1:])<=len(project.issues):
            issue=project.issues[int(fid[1:])-1]
            issues.append({'message':issue.message,'locations':issue.evidence,'excluded':issue.excluded})
    local=group.get('basis')=='Sourced part rule'
    return {'group_id':group['id'],'basis':'Manufacturer requirement' if local else group.get('basis','Local check'),
        'identity_checks':identity,'observed':group.get('summary',''),'locations':group.get('evidence',[]),'rule_findings':issues[:6],
        'pictures':pictures,'sources':group.get('sources',[]),'notices':notices,
        'limit':'The datasheet establishes the requirement; the project file establishes the recorded connection. Physical copper, operating mode and the exact part still need to agree.' if local else
                'This is a concern to review, not a proven hardware failure.' if group.get('priority')!='fix' else
                'This conflict was found in the saved files. Recheck after changing the design.',
        'image_status':'ready' if pictures else 'not_available'}
