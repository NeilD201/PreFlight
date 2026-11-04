"""Read-only connections to explicitly selected, locally saved CAD documents.

Watching a file is separate from interpreting its format. No converter, cloud
login, editor API, AI call or write to a design file is implied by a connection.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import stat
import threading


EDITORS = {
    'fusion': {'name': 'Autodesk Fusion Electronics', 'extensions': ['.sch', '.brd'], 'adapter':'fusion',
               'review_available': False, 'help': 'Connect with the PreflightSync add-in. It exports the linked PCB and schematic when saved in Fusion. Experimental sync; native analysis is not available yet.'},
    'kicad': {'name': 'KiCad', 'extensions': ['.kicad_pro', '.kicad_sch', '.kicad_pcb', '.kicad_dru'],
              'review_available': True, 'help': 'Select the original project, schematic and board files. Saved changes are detected automatically.'},
    'eagle': {'name': 'Autodesk EAGLE / Fusion exports', 'extensions': ['.sch', '.brd', '.lbr'],
              'review_available': False, 'help': 'Local EAGLE XML files can be watched. Fusion cloud documents need an export adapter; exporting once does not create live Fusion sync.'},
    'easyeda': {'name': 'EasyEDA local files', 'extensions': ['.json', '.epro'],
                'review_available': False, 'help': 'Watch a local EasyEDA source file that your editor saves. A browser download is a snapshot; it does not connect the browser editor.'},
    'altium': {'name': 'Altium Designer', 'extensions': ['.prjpcb', '.schdoc', '.pcbdoc'],
               'review_available': False, 'help': 'Select the original local project, schematic sheets and PCB. Cloud-only projects must be saved locally first.'},
    'orcad': {'name': 'OrCAD / Allegro', 'extensions': ['.opj', '.dsn', '.brd'],
              'review_available': False, 'help': 'Select the original local design files. Preflight tracks saves; a native analysis reader is not available yet.'},
    'pads': {'name': 'Siemens PADS', 'extensions': ['.prj', '.sch', '.pcb', '.asc'],
             'review_available': False, 'help': 'Select the original local design files. An exported file updates only when the editor exports it again.'},
}
MAX_FILE = 100 * 1024 * 1024
MAX_TOTAL = 250 * 1024 * 1024


def stamp():
    return datetime.now(timezone.utc).isoformat()


class ConnectionError(ValueError):
    pass


def metadata(paths):
    result = []
    total = 0
    for raw in paths:
        path = Path(raw)
        if path.is_symlink():
            raise ConnectionError(f'{path.name} became a symbolic link. Reconnect the original file.')
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ConnectionError(f'{path.name} is not a regular file.')
        if not 0 < info.st_size <= MAX_FILE:
            raise ConnectionError(f'{path.name} is empty or exceeds the 100 MB connection limit.')
        total += info.st_size
        result.append((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    if total > MAX_TOTAL:
        raise ConnectionError('Selected files exceed the 250 MB connection limit.')
    return result


def fingerprints(paths):
    before = metadata(paths)
    hashes = {}
    for raw in paths:
        digest = hashlib.sha256()
        size = 0
        with Path(raw).open('rb') as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE:
                    raise ConnectionError('A file grew beyond the connection limit while being read.')
                digest.update(chunk)
        hashes[raw] = digest.hexdigest()
    if before != metadata(paths):
        raise ConnectionError('Files are still being saved. Waiting for a stable version.')
    return hashes, before


class EditorConnections:
    def __init__(self, store, *, start=True):
        self.store = store
        self.lock = threading.RLock()
        self.records = store.preference('editor_connections_v1') or []
        self.observed = {}
        self.committed = {}
        self.stop = threading.Event()
        self.thread = None
        if start:
            self.thread = threading.Thread(target=self._watch, daemon=True, name='preflight-editor-saves')
            self.thread.start()

    def _persist(self):
        self.store.preference('editor_connections_v1', self.records)

    def connect(self, editor, paths):
        if not isinstance(editor, str) or editor not in EDITORS:
            raise ConnectionError('Choose a supported editor connection.')
        if editor == 'fusion':
            raise ConnectionError('Use Set up Fusion sync to connect the editor add-in.')
        if not isinstance(paths, list) or not 1 <= len(paths) <= 50:
            raise ConnectionError('Select between 1 and 50 local design files.')
        selected = []
        for raw in paths:
            if not isinstance(raw, str) or not raw or len(raw) > 4096 or any(ord(c) < 32 for c in raw):
                raise ConnectionError('Enter one complete local file path per line.')
            path = Path(raw).expanduser()
            if not path.is_absolute() or path.is_symlink():
                raise ConnectionError('Select original files using their full paths, not symbolic links.')
            path = path.resolve(strict=True)
            if path.suffix.lower() not in EDITORS[editor]['extensions']:
                raise ConnectionError(f'{path.name} is not a supported file for {EDITORS[editor]["name"]}.')
            selected.append(str(path))
        selected = sorted(set(selected))
        if editor == 'kicad':
            for extension in ('.kicad_pro', '.kicad_pcb'):
                if sum(Path(p).suffix == extension for p in selected) != 1:
                    raise ConnectionError('Select one KiCad project, one PCB and its schematic sheets.')
            if not any(p.endswith('.kicad_sch') for p in selected):
                raise ConnectionError('Select the KiCad schematic too.')
            pro = next(Path(p) for p in selected if p.endswith('.kicad_pro'))
            pcb = next(Path(p) for p in selected if p.endswith('.kicad_pcb'))
            if pro.with_suffix('.kicad_pcb') != pcb:
                raise ConnectionError('The KiCad PCB and project must have the same name and folder.')
            if any(not Path(p).is_relative_to(pro.parent) for p in selected):
                raise ConnectionError('Select schematic sheets within the KiCad project folder.')
        if editor == 'eagle':
            for path in selected:
                if Path(path).suffix.lower() in ('.sch', '.brd'):
                    with open(path, 'rb') as source:
                        if b'<eagle' not in source.read(4096):
                            raise ConnectionError('This is not EAGLE XML. Choose EAGLE 6+ files or Fusion EAGLE exports.')
        hashes, stats = fingerprints(selected)
        with self.lock:
            existing = next((r for r in self.records if r['editor'] == editor and r['files'] == selected), None)
            if existing:
                return deepcopy(existing)
            if len(self.records) >= 12:
                raise ConnectionError('Disconnect an unused project before adding another (12 connections maximum).')
            record = {'id': secrets.token_hex(12), 'editor': editor, 'name': Path(selected[0]).stem,
                      'files': selected, 'hashes': hashes, 'revision': 1, 'connected_at': stamp(),
                      'saved_at': stamp(), 'status': 'watching', 'message': 'Watching the original saved files.'}
            self.records.append(record)
            try:
                self._persist()
            except Exception:
                self.records.remove(record)
                raise
            self.committed[record['id']] = stats
            return deepcopy(record)

    def disconnect(self, connection_id):
        if not isinstance(connection_id, str) or len(connection_id) != 24:
            raise ConnectionError('Choose an existing editor connection.')
        with self.lock:
            previous = self.records
            fusion = next((r for r in previous if r['id']==connection_id and r['editor']=='fusion'),None)
            if fusion:
                root=Path(fusion['sync_folder']);temporary=root/'control.tmp'
                temporary.write_text(json.dumps({'enabled':False}));temporary.replace(root/'control.json')
            self.records = [r for r in previous if r['id'] != connection_id]
            try:
                self._persist()
            except Exception:
                self.records = previous
                raise
            self.committed.pop(connection_id, None)
            self.observed.pop(connection_id, None)

    def scan(self):
        # Hash outside the lock so slow files cannot stall the browser's polling.
        with self.lock:
            pending = deepcopy(self.records)
        for item in pending:
            identifier = item['id']
            try:
                if item['editor']=='fusion':
                    self._scan_fusion(item)
                    continue
                current = metadata(item['files'])
                with self.lock:
                    record = next((r for r in self.records if r['id'] == identifier), None)
                    if record is None:
                        continue
                    if current == self.committed.get(identifier):
                        record.update(status='watching', message='Watching the original saved files.')
                        continue
                    if current != self.observed.get(identifier):
                        self.observed[identifier] = current
                        record.update(status='saving', message='Save detected. Waiting for files to settle…')
                        continue
                hashes, current = fingerprints(item['files'])
                with self.lock:
                    record = next((r for r in self.records if r['id'] == identifier), None)
                    if record is None:
                        continue
                    if hashes != record['hashes']:
                        previous = deepcopy(record)
                        record.update(hashes=hashes, revision=record['revision'] + 1, saved_at=stamp())
                        try:
                            self._persist()
                        except Exception:
                            record.update(previous)
                            raise
                    self.committed[identifier] = current
                    record.update(status='watching', message='Watching the original saved files.')
            except (OSError, ValueError):
                with self.lock:
                    record = next((r for r in self.records if r['id'] == identifier), None)
                    if record is not None:
                        record.update(status='unavailable', message='A selected file cannot be read. It may be saving, moved or deleted. Waiting for it to return.')

    def prepare_fusion(self):
        with self.lock:
            existing=next((r for r in self.records if r['editor']=='fusion'),None)
            if existing:return deepcopy(existing)
            if len(self.records)>=12:raise ConnectionError('Disconnect an unused project first.')
            identifier=secrets.token_hex(12)
            root=self.store.root.resolve()/'.editor-sync'/identifier
            root.mkdir(parents=True)
            addon=root/'PreflightSync'
            shutil.copytree(Path(__file__).parent/'connectors'/'fusion'/'PreflightSync',addon,
                            ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            (addon/'config.json').write_text(json.dumps({'sync_folder':str(root)}))
            (root/'control.json').write_text(json.dumps({'enabled':True}))
            record={'id':identifier,'editor':'fusion','name':'Fusion project','files':[], 'hashes':{},
                    'revision':0,'connected_at':stamp(),'saved_at':None,'status':'waiting',
                    'message':'Open the Electronics project in Fusion and run the add-in. No files received yet.',
                    'sync_folder':str(root),'addon_folder':str(addon)}
            self.records.append(record)
            try:self._persist()
            except Exception:
                self.records.remove(record)
                raise
            return deepcopy(record)

    def _scan_fusion(self, item):
        root=Path(item['sync_folder'])
        state={'status':'waiting','message':'Waiting for the Fusion add-in. No files received yet.'}
        if (root/'status.json').is_file():
            if (root/'status.json').stat().st_size>8192:raise ConnectionError('Invalid Fusion status.')
            state=json.loads((root/'status.json').read_text())
        accepted=None
        current=root/'current.json'
        if current.is_file():
            if current.stat().st_size>8192:raise ConnectionError('Invalid Fusion snapshot.')
            manifest=json.loads(current.read_text())
            generation=manifest.get('generation','')
            if (not isinstance(generation,str) or not re.fullmatch(r'save-[0-9a-f]{32}',generation)
                    or manifest.get('files')!=['design.sch','design.brd'] or not isinstance(manifest.get('identity'),str)):
                raise ConnectionError('Invalid Fusion snapshot.')
            folder=root/generation
            if folder.is_symlink():raise ConnectionError('Invalid Fusion snapshot folder.')
            paths=[str(folder/name) for name in manifest['files']]
            metadata(paths)
            if generation != item.get('generation'):
                hashes,_=fingerprints(paths)
                for path in paths:
                    with open(path,'rb') as source:
                        if b'<eagle' not in source.read(4096):raise ConnectionError('Fusion export is not EAGLE XML.')
                accepted=(manifest,paths,hashes)
        with self.lock:
            record=next((r for r in self.records if r['id']==item['id']),None)
            if record is None:return
            if accepted:
                manifest,paths,hashes=accepted
                if record.get('identity') and record['identity']!=manifest['identity']:
                    raise ConnectionError('Fusion changed project identity. Reconnect the intended project.')
                previous=deepcopy(record)
                record.update(files=paths,hashes=hashes,generation=manifest['generation'],identity=manifest['identity'],
                              name=str(manifest.get('name','Fusion project'))[:120],revision=record['revision']+1,saved_at=stamp())
                try:self._persist()
                except Exception:
                    record.update(previous)
                    raise
            status=state.get('status')
            if status=='watching' and not record['revision']:status='waiting'
            record.update(status=status if status in ('waiting','saving','watching','unavailable','stopped') else 'unavailable',
                          message=str(state.get('message','Fusion sync status unavailable.'))[:1000])

    def listing(self):
        with self.lock:
            return {'editors': deepcopy(EDITORS), 'connections': [
                {k: deepcopy(v) for k, v in r.items() if k != 'hashes'} for r in self.records]}

    def get(self, identifier):
        with self.lock:
            record = next((r for r in self.records if r['id'] == identifier), None)
            if not record:
                raise ConnectionError('This editor connection no longer exists.')
            return deepcopy(record)

    def _watch(self):
        # Existing users already selected original KiCad paths. Carry that
        # connection into the new screen without blocking app startup.
        try:
            if self.store.preference('editor_legacy_migrated') is not True:
                self.store.preference('editor_legacy_migrated', True)
                linked = self.store.preference('linked_kicad_files')
                if linked and not any(r['editor'] == 'kicad' for r in self.records):
                    extra = [str(Path(linked['path']) / name) for name in linked.get('sheets') or [] if name != linked.get('schematic')]
                    self.connect('kicad', list(linked['files'].values()) + extra)
        except Exception:
            pass  # A moved legacy project can be selected again in Editors.
        while not self.stop.wait(3):
            try:
                self.scan()
            except Exception:
                # A full/unavailable workspace database must not kill monitoring.
                with self.lock:
                    for record in self.records:
                        record.update(status='unavailable', message='Could not save connection status. Check available disk space.')

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
