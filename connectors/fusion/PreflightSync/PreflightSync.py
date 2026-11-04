"""Fusion Electronics preview add-in: export the selected design after saves.

No network calls or edits to Fusion documents. Preflight supplies config.json.
"""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import uuid

_handlers = []
_app = None
_root = None
_identity = None
_exporting = False


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(path)


def status(state, message):
    if _root:
        atomic_json(_root/'status.json', {'status': state, 'message': message})


def pair(document, electron):
    for i in range(document.products.count):
        product = document.products.item(i)
        design = electron.EcadDesign.cast(product)
        board = electron.Board.cast(product)
        schematic = electron.Schematic.cast(product)
        if design:
            return design.board, design.schematic
        if board:
            return board, board.linkedSchematic
        if schematic:
            return schematic.linkedBoard, schematic
    return None, None


def design_identity(board, schematic):
    identities = []
    for product in (board, schematic):
        document = product.parentDocument
        if not document.isSaved or not document.dataFile:
            raise ValueError('Save the board and schematic in Fusion before connecting.')
        identities.append(document.dataFile.id)
    return hashlib.sha256(json.dumps(identities).encode()).hexdigest()


def export_pair(root, board, schematic, identity):
    """Publish one complete generation; never expose half of a new pair."""
    if any(product.parentDocument.isModified for product in (board, schematic)):
        raise ValueError('Save both the schematic and PCB. Unsaved edits are not synchronized.')
    draft = Path(tempfile.mkdtemp(prefix='draft-', dir=root))
    try:
        for product, suffix, factory in (
            (board, '.brd', 'createEagleBrdExportOptions'),
            (schematic, '.sch', 'createEagleSchExportOptions')):
            manager = product.exportManager
            options = getattr(manager, factory)(str(draft/('design'+suffix)))
            if options is None or not manager.execute(options):
                raise ValueError('Fusion could not export both Electronics files. The previous snapshot is preserved.')
            path = draft/('design'+suffix)
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError('Fusion returned an empty export. The previous snapshot is preserved.')
        if any(product.parentDocument.isModified for product in (board, schematic)):
            raise ValueError('The design changed during export. Save both files again.')
        generation = 'save-'+uuid.uuid4().hex
        draft.rename(root/generation)
        atomic_json(root/'current.json', {'schema':1, 'identity':identity,
                    'generation':generation, 'name':str(board.name), 'files':['design.sch','design.brd']})
        # Keep the current and previous generations only; remove managed exports.
        older = sorted(root.glob('save-*'), key=lambda p:p.stat().st_mtime, reverse=True)
        for path in older[2:]:
            if path.is_dir() and not path.is_symlink() and len(path.name)==37:
                shutil.rmtree(path)
    finally:
        if draft.exists():
            shutil.rmtree(draft)


def synchronize(document):
    global _identity, _exporting
    if _exporting or document is None:
        return
    import adsk.electron
    try:
        if not json.loads((_root/'control.json').read_text()).get('enabled'):
            return
        board, schematic = pair(document, adsk.electron)
        if board is None or schematic is None:
            if _identity is None:status('waiting', 'Open a linked Electronics PCB and schematic, then save both.')
            return
        identity = design_identity(board, schematic)
        if _identity and identity != _identity:
            return  # Never switch to an unrelated project on another tab's save.
        if _identity is None:
            _identity = identity
            atomic_json(_root/'binding.json', {'identity':identity})
        _exporting = True
        status('saving', 'Exporting the saved Fusion PCB and schematic…')
        export_pair(_root, board, schematic, identity)
        status('watching', 'Fusion save synchronized. Native analysis is not available yet.')
    except Exception as exc:
        status('unavailable', str(exc))
        if _app:_app.log('Preflight sync: '+str(exc))
    finally:
        _exporting = False


def run(context):
    global _app, _root, _identity
    import adsk.core
    try:
        import adsk.electron
        _app = adsk.core.Application.get()
        config = json.loads((Path(__file__).parent/'config.json').read_text())
        _root = Path(config['sync_folder'])
        if not _root.is_dir() or not (_root/'control.json').is_file():
            raise ValueError('Set up Fusion sync in Preflight → Editors first.')
        _identity = json.loads((_root/'binding.json').read_text())['identity'] if (_root/'binding.json').exists() else None
        if _handlers:return
        if _identity is None:
            if _app.activeDocument is None:raise ValueError('Open the Electronics project, then run PreflightSync again.')
            board, schematic=pair(_app.activeDocument,adsk.electron)
            if board is None or schematic is None:raise ValueError('Open the linked Electronics PCB or schematic, then run PreflightSync again.')
            _identity=design_identity(board,schematic)
            atomic_json(_root/'binding.json',{'identity':_identity})
        class Saved(adsk.core.DocumentEventHandler):
            def notify(self, args):
                synchronize(adsk.core.DocumentEventArgs.cast(args).document)
        handler = Saved()
        _app.documentSaved.add(handler)
        _handlers.append(handler)
        status('waiting', 'Add-in running. Open the Electronics project and save both files.')
        synchronize(_app.activeDocument)
    except Exception as exc:
        if _app:_app.userInterface.messageBox('Preflight sync could not start: '+str(exc))
        status('unavailable', str(exc))


def stop(context):
    for handler in _handlers:
        if _app:_app.documentSaved.remove(handler)
    _handlers.clear()
    status('stopped', 'Fusion add-in stopped. Run PreflightSync in Fusion to resume.')
