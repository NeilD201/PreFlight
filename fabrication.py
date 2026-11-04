"""Prepare a consistent KiCad fabrication snapshot; never modify source files."""
import hashlib
import io
import json
import re
from pathlib import Path
import subprocess
import sys
import zipfile

MAX_PACKAGE = 50 * 1024 * 1024


def validate_layers(files, copper_count):
    """Use Gerber layer attributes: user-renamed copper layers change filenames."""
    functions = []
    for name, content in files.items():
        if not name.endswith('.gbr'):
            continue
        match = re.search(rb'%TF\.FileFunction,([^*]+)\*%', content)
        if not match or b'M02*' not in content or b'%FS' not in content:
            raise ValueError('KiCad returned an incomplete Gerber layer: '+name)
        fields = match[1].decode('ascii').split(',')
        fields[0] = {'soldermask':'SolderMask', 'paste':'SolderPaste', 'solderpaste':'SolderPaste'}.get(fields[0].lower(), fields[0])
        functions.append(fields)
    expected = [('Copper', 'L'+str(i)) for i in range(1,copper_count+1)]
    expected += [(kind,side) for kind in ('SolderMask','Legend','SolderPaste') for side in ('Top','Bot')]
    expected += [('Profile',)]
    if any(sum(tuple(f[:len(prefix)]) == prefix for f in functions) != 1 for prefix in expected):
        raise ValueError('KiCad did not export every requested fabrication layer exactly once.')


def prepare_board(binary, board, directory):
    candidates = [Path(binary).resolve().parent.parent/'Frameworks/Python.framework/Versions/Current/bin/python3',
                  Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3'),
                  Path(sys.executable)]
    runtime = None
    for candidate in dict.fromkeys(candidates):
        if not candidate.is_file():
            continue
        try:
            probe = subprocess.run([str(candidate), '-c', 'import pcbnew'], capture_output=True, timeout=10)
            if probe.returncode == 0:
                runtime = candidate
                break
        except (OSError, subprocess.TimeoutExpired):
            continue
    if not runtime:
        return {'status':'unavailable', 'message':'KiCad Python is unavailable; copper refill and production-file export did not run.'}
    output = directory/'prepared-board.json'
    try:
        result = subprocess.run([str(runtime), str(Path(__file__).with_name('_pcb_prepare.py')), str(board), str(output)],
                                cwd=directory, capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise ValueError('KiCad could not prepare the copied PCB: '+result.stderr[-900:])
        metadata = json.loads(output.read_text())
        if metadata.get('status') != 'prepared':
            raise ValueError('KiCad returned incomplete preparation evidence.')
        metadata['analyzed_pcb_sha256'] = hashlib.sha256(board.read_bytes()).hexdigest()
        from .routing_geometry import measure
        from .parsers.sexpr import parse
        try:
            metadata['signal_geometry'] = measure(parse(board.read_text(), 'kicad_pcb'))
        except ValueError as exc:
            metadata['signal_geometry'] = {'status':'unavailable','message':'Routing measurement failed: '+str(exc)}
        return metadata
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {'status':'failed', 'message':str(exc).replace(str(directory)+'/', '')}


def export_fabrication(binary, board, directory, prepared, source_hashes):
    from .native import _run
    if prepared.get('status') != 'prepared':
        return {'status':'unavailable', 'message':'Production files require a successfully prepared board copy.'}, {}
    snapshot = {'analyzed-board.kicad_pcb':board.read_bytes()}
    basis = {'analyzed_pcb_sha256':prepared['analyzed_pcb_sha256']}
    if not prepared.get('outline_closed'):
        return {**basis, 'status':'blocked', 'message':'Fix the missing or invalid board outline before generating production files.'}, snapshot
    output = directory/'fabrication-output'
    output.mkdir()
    layers = prepared['copper_layers'] + ['F.Mask','B.Mask','F.Silkscreen','B.Silkscreen','Edge.Cuts','F.Paste','B.Paste']
    try:
        _run(binary, ['pcb','export','gerbers','--layers',','.join(layers),'--no-protel-ext','--use-drill-file-origin','--output',str(output)+'/',str(board)], directory)
        _run(binary, ['pcb','export','drill','--format','excellon','--drill-origin','plot','--excellon-units','mm','--excellon-separate-th','--output',str(output)+'/',str(board)], directory)
        files = {p.name:p.read_bytes() for p in output.iterdir() if p.is_file()}
        if not files or sum(map(len,files.values())) > MAX_PACKAGE or not any(n.endswith('.drl') for n in files):
            raise ValueError('Missing drill output or oversized fabrication files.')
        validate_layers(files, len(prepared['copper_layers']))
        if any(b'M48' not in b or b'M30' not in b for n,b in files.items() if n.endswith('.drl')):
            raise ValueError('KiCad returned incomplete drill output.')
        manifest = {'schema_version':1, 'status':'draft', 'source_hashes':source_hashes,
                    'analyzed_pcb_sha256':prepared['analyzed_pcb_sha256'], 'layers':layers,
                    'coordinate_origin':'KiCad drill/place origin for both Gerber and drill',
                    'files':{n:hashlib.sha256(b).hexdigest() for n,b in files.items()}}
        files['manifest.json'] = json.dumps(manifest,indent=2).encode()
        files['READ-ME.txt'] = ('DRAFT FABRICATION FILES\nGenerated from the temporary board after refilling copper.\n'
            'Read the Before ordering review. Export success is not manufacturing approval.\n'
            'Inspect these layers and drills in the manufacturer preview. Confirm stackup, finish, holes, routing and order options.\n'
            'Paste layers are included for stencil preparation. Assembly BOM/CPL are separate.\n').encode()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
            for name,content in files.items():
                bundle.writestr(name,content)
        package = archive.getvalue()
        metadata = {**manifest, 'file_count':len(manifest['files']), 'package_sha256':hashlib.sha256(package).hexdigest()}
        return metadata, {**snapshot, 'fabrication-draft.zip':package}
    except (OSError, ValueError) as exc:
        return {**basis, 'status':'failed', 'message':str(exc).replace(str(directory)+'/', '')}, snapshot


def previous_fabrication(folder, metadata):
    assets = {}
    for name,key in [('fabrication-draft.zip','package_sha256'),('analyzed-board.kicad_pcb','analyzed_pcb_sha256')]:
        path = Path(folder)/name
        if not metadata.get(key) or not path.is_file() or path.stat().st_size > MAX_PACKAGE:
            continue
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() == metadata[key]:
            assets[name] = content
    return assets
