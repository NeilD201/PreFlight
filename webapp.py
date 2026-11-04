"""Local browser app for the existing checker. Python standard library only."""

from copy import deepcopy
from dataclasses import asdict, fields
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import gzip
import hashlib
import io
import json
import math
import os
import shutil
import traceback
import zipfile
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
from urllib.parse import parse_qs, unquote, urlsplit
import webbrowser

from .ai.reviewer import AIError, PROVIDERS, check_ai_connection, run_ai_review
from .ai.meta import (is_meta, model_id as meta_model_id, DEFAULT_MODEL as META_MODEL,
                      PRICING_USD_PER_M as META_PRICING, meta_action, set_usage_recorder,
                      clear_usage_recorder)
from .ai.usage import LIMITS, request_budget, current_budget
from .breadboard_to_PCB import livescan as livescan_store
from .firmware.build_spec import build_firmware_spec
from .firmware.generator import generate_firmware as build_firmware_project
from .firmware.plan import build_firmware_plan, review_plan_with_meta
from .firmware.questions import extras_from_answers, route_questions
from .product_context import validate_answers
from .analysis import analyze_project
from .archive import ArchiveError, MAX_ARCHIVE_BYTES, read_project_file
from .models import Component, ComponentRecord, Issue, Pad, Pin, Project, ProjectFile
from .report import save_report
from .storage import RevisionConflict, WorkspaceStore
from .editor_connections import EditorConnections, ConnectionError as EditorConnectionError, EDITORS
from .workflow import compare_runs, keyed_groups, readiness, review_bundle
from .datasheets import collect_datasheets, excerpts
from .assembly_export import generate_assembly
from .manufacturing import evaluate as evaluate_manufacturing, measure_source, validate_selection
from .design_review import context_key, empty_context, edit_context, local_plan, compose_plan, refresh_context_from_store
from .board_visual import render_board, previous_visual
from .step_help import answer_step
from .project_chat import answer_project
from .design_review import current_plan
from .schematic_visual import render_schematic, previous_schematic
from .issue_proof import issue_proof
from .credential_store import (Keychain, KeychainError, account_for, available as keychain_available,
                                META_SERVICE, meta_account)
from .ai.reviewer import RoutedKey, redact_error
from . import file_fixes
from . import fix_decision, fix_plan
from .chat_safety import guarded_chat, evidence_state

ASSETS = Path(__file__).parent / 'web'
BB_ASSETS = Path(__file__).parent / 'breadboard_to_PCB'


def _asset_tag():
    """Cache-buster that changes whenever a shipped web file changes."""
    try:
        stamps = [p.stat().st_mtime for p in ASSETS.rglob('*') if p.is_file()]
        return str(int(max(stamps))) if stamps else '0'
    except OSError:
        return '0'


ASSET_TAG = _asset_tag()


def _cache_busted_html(body: bytes) -> bytes:
    """Stamp script/stylesheet URLs so browsers never run stale interface code."""
    text = body.decode('utf-8')
    text = text.replace('.js"', '.js?v=' + ASSET_TAG + '"')
    text = text.replace('.css"', '.css?v=' + ASSET_TAG + '"')
    return text.encode('utf-8')
# Shown at the top of the app so a restart serving new code is visible.
# Bump this with every user-facing change.
APP_VERSION = 'v1.04'
FILE_FIX_STALL_TIMEOUT_SECONDS = 20 * 60
REPORT_FILES = {'report.html', 'report.json', 'ai-review.json', 'ai-input.json', 'ai-prompt.txt',
                'kicad-erc.json', 'kicad-drc.json', 'kicad-expanded-erc.json', 'kicad-expanded-drc.json', 'bom-draft.csv', 'cpl-draft.csv',
                'sourcing-review.csv', 'assembly-notes.txt', 'assembly-manifest.json', 'assembly-draft.zip',
                'board-front.svg', 'board-back.svg', 'board-visual.json', 'schematic.svg', 'schematic-visual.json',
                'before-ordering.json', 'fabrication-draft.zip', 'analyzed-board.kicad_pcb'}


def now():
    return datetime.now(timezone.utc).isoformat()


LOW_DISK_BYTES = 5*1024**3


def disk_warning(path):
    """Warn before SQLite starts failing on a nearly full volume."""
    try:
        free = shutil.disk_usage(path).free
    except OSError:
        return ''
    if free >= LOW_DISK_BYTES:
        return ''
    return (f'Warning: only {free/1024**3:.1f} GB of disk space is free. '
            'Free space before running a check; reviews cannot be saved or loaded on a full disk.')


def fix_source_message(store, report_id):
    """Explain exactly why Fix Design has no live folder for this review.

    Reviews analyzed from uploaded copies carry no editable path, and saved
    folders can move or unmount. The bare 'connect the folder' error sent
    users in circles, so each cause names itself and the next step.
    """
    saved = store.preference('input:' + report_id) or {}
    if saved.get('upload_name'):
        return ('This review was analyzed from the uploaded copy %s, which Fix Design cannot edit. '
                'Link the live KiCad folder (type the folder path or Browse — after the first link, '
                'dropping the files needs no second dialog), run Check again, then use Fix Design. '
                'Your uploaded review stays unchanged.' % saved['upload_name'])
    if saved.get('path'):
        return ('The saved KiCad folder is no longer there: %s. Reconnect it with the folder option '
                'on this review, then prepare the fix again. Nothing was changed.' % saved['path'])
    return 'Connect the saved KiCad folder first.'


def prior_design_answers(records, report_id, group_id, source_hashes, reports):
    """Carry approved user context into a retry only for the same finding and exact source revision."""
    candidates=[]
    for record in records.values():
        if (record.get('kind')!='design_plan' or record.get('report_id')!=report_id or
                record.get('group_id')!=group_id or not record.get('design_answers') or
                record.get('hashes')!=source_hashes):
            continue
        try:
            modified=(Path(reports)/'.file-fixes'/record['id']/'plan.json').stat().st_mtime
        except (KeyError,OSError):
            modified=0
        candidates.append((modified,record.get('design_answers',{})))
    if not candidates:return {}
    return dict(max(candidates,key=lambda item:item[0])[1])


def restore_project(data):
    """Restore only known dataclass fields from a report generated by this app."""
    def record(cls, value):
        return cls(**{f.name: value[f.name] for f in fields(cls) if f.name in value})
    project = record(Project, data)
    project.files = [record(ProjectFile, f) for f in data.get('files', [])]
    project.issues = [record(Issue, i) for i in data.get('issues', [])]
    project.components = {}
    for ref, c in data.get('components', {}).items():
        component = record(Component, c)
        component.schematic_pins = [record(Pin, pin) for pin in c.get('schematic_pins', [])]
        for source in ('pcb', 'schematic'):
            records = []
            for r in c.get(source, []):
                item = record(ComponentRecord, r)
                item.pads = [record(Pad, pad) for pad in r.get('pads', [])]
                records.append(item)
            setattr(component, source, records)
        project.components[ref] = component
    sync_ai_summary(project.ai_review)
    return project


def sync_ai_summary(ai_review):
    """Keep a repaired-review summary consistent with its final validated count."""
    if not isinstance(ai_review, dict) or ai_review.get('status') != 'completed':
        return
    result = ai_review.get('result')
    coverage = ai_review.get('coverage')
    if not isinstance(result, dict) or not isinstance(coverage, dict):
        return
    summary = result.get('summary', '')
    if not isinstance(summary, str) or not summary.startswith('Evidence checks accepted AI guidance for '):
        return
    accepted, requested = coverage.get('validated_actions'), coverage.get('requested_actions')
    if (type(accepted) is not int or type(requested) is not int or
            accepted < 0 or requested < accepted):
        return
    if accepted == requested:
        result['summary'] = f'Evidence checks accepted AI guidance for all {requested} issues.'
    else:
        result['summary'] = f'Evidence checks accepted AI guidance for {accepted} of {requested} issues. Other issues keep local check guidance.'


class AppError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class JobCancelled(Exception):
    pass


class AppState:
    def __init__(self, default_path, reports, *, key='', model=None, kicad_cli=None, ai=None, native=True,
                 adopt_keychain=False):
        """When adopt_keychain is set (real server startup), recognize a Meta
        Keychain entry this workspace never recorded, unless the user explicitly
        declined remembering it or selected another provider."""
        self.default_path = str(default_path)
        self.reports = Path(reports).resolve()
        self.key = key
        self.openai_key = os.environ.get('OPENAI_API_KEY', '')
        self.openai_key_saved = False
        self.meta_key = os.environ.get('LLAMA_API_KEY', '')
        self.meta_key_saved = False
        self.fallback_keys = []
        self.model = model or PROVIDERS['meta']['model']
        self.kicad_cli = kicad_cli
        self.ai_default = bool(key) if ai is None else ai
        self.native_default = native
        self.token = secrets.token_urlsafe(32)
        # Identifies this server process. The frontend compares it after any
        # auth-token refresh: a changed id means the server restarted under an
        # open tab, so the tab offers a one-click reload instead of silently
        # running stale interface code.
        self.server_id = secrets.token_hex(8)
        self.lock = threading.RLock()
        self.chat_requests = set()
        self.job = None
        # Latest partial VECTOR pipeline snapshot, published by the worker
        # thread while a detailed investigation runs. Display-only; the saved
        # report remains the source of truth once the job completes.
        self.live_pipeline = None
        self.project = None
        self.report_id = None
        self.revision = 0
        self.worker = None
        self.fix_worker = None
        self.fix_design_worker = None
        self.cancel_event = threading.Event()
        self.uploading = False
        self.upload = None
        self.file_fixes = {}
        self.temp = tempfile.TemporaryDirectory(prefix='pcb-preflight-web-')
        self.store = WorkspaceStore(self.reports)
        self.editor_connections = EditorConnections(self.store)
        self._usage_cache = None
        set_usage_recorder(self.store.record_meta_usage)
        self.gemini_model = self.store.preference('gemini_model') or PROVIDERS['gemini']['model']
        if model is None:self.model=self.store.preference('ai_model') or self._default_model()
        self.gemini_backup_enabled = self.store.preference('gemini_backup_enabled') is not False
        self.key_saved = False
        self.key_storage_error = ''
        self._adopt_keychain = bool(adopt_keychain)
        remember_meta = self.store.preference('remember_meta_key')
        check_meta = bool(remember_meta) or (self._adopt_keychain and remember_meta is None and keychain_available())
        self.keychain_loading = bool(self.store.preference('remember_gemini_key') or self.store.preference('remember_openai_key') or check_meta)
        self._credential_revision = 0
        self.connection_test = None
        if self.keychain_loading:
            # Security.framework can wait for macOS approval or an unlocked
            # login keychain. Keep that wait off the server startup thread.
            credential_revision = self._credential_revision
            self.keychain_thread = threading.Thread(
                target=self._restore_saved_keys, args=(credential_revision,), daemon=True,
                name='pcb-preflight-keychain-restore')
            self.keychain_thread.start()
        else:
            self.keychain_thread = None
        if ai is None:self.ai_default=bool(self.keys_for_ai())
        # Loading every historical plan can stall startup on filesystems that
        # hydrate older report files lazily. The current workspace only needs
        # its most recent few plans for retry/approval state; older reports
        # remain on disk and can be reopened from history when needed.
        manifests=sorted((self.reports/'.file-fixes').glob('*/plan.json'),key=lambda p:p.stat().st_mtime)[-5:]
        for manifest in manifests:
            try:
                if manifest.stat().st_size>64*1024:
                    continue
                record = json.loads(manifest.read_text())
                if re.fullmatch(r'[0-9a-f]{32}', manifest.parent.name) and record.get('id') == manifest.parent.name:
                    if record.get('status')=='blocked' and ('No space left on device' in str(record.get('message','')) or 'Errno 28' in str(record.get('message',''))):
                        record['message']='Your Mac ran out of storage while preparing the edit preview. The project files were not changed. Free some disk space, then retry the same edit.'
                    if record.get('status') == 'preparing':record.update(status='blocked', message='Preparation was interrupted. Prepare the fix again.')
                    if record.get('kind')=='design_plan' and record.get('design_version')!=9:
                        record.update(status='blocked',message='The design evidence checks were updated. Investigate this issue again to prepare a current proposal.')
                    self.file_fixes[record['id']] = record
            except (OSError, ValueError, AttributeError):
                pass
        self.fix_designs = {}
        for manifest in sorted((self.reports/'.fix-design').glob('*/record.json'),key=lambda p:p.stat().st_mtime)[-5:]:
            try:
                if manifest.stat().st_size>256*1024:
                    continue
                record = json.loads(manifest.read_text())
                if re.fullmatch(r'[0-9a-f]{32}', manifest.parent.name) and record.get('id') == manifest.parent.name:
                    if record.get('status') == 'preparing':
                        record.update(status='blocked', message='Coordinated-fix preparation was interrupted. Prepare the fix again.')
                    self.fix_designs[record['id']] = record
            except (OSError, ValueError, AttributeError):
                pass
        self.firmware_jobs = {}
        self.firmware_worker = None
        for manifest in sorted((self.reports/'.firmware').glob('*/record.json'),key=lambda p:p.stat().st_mtime)[-5:]:
            try:
                if manifest.stat().st_size>256*1024:
                    continue
                record = json.loads(manifest.read_text())
                if re.fullmatch(r'[0-9a-f]{32}', manifest.parent.name) and record.get('id') == manifest.parent.name:
                    if record.get('status') == 'preparing':
                        record.update(status='blocked', message='Firmware discovery was interrupted. Create the firmware again.')
                    self.firmware_jobs[record['id']] = record
            except (OSError, ValueError, AttributeError):
                pass
        self.sync_history()
        last = self.store.preference('last_report')
        if last:
            try:
                self.open_report(last)
            except AppError:
                pass

    def _restore_saved_keys(self, revision):
        """Load remembered credentials without blocking local app startup.

        Each service loads independently: one denied prompt must not discard
        the keys that did load. A denied Meta entry skips its legacy fallback
        instead of raising a second prompt.
        """
        saved = openai_saved = meta_saved = ''
        errors = []
        if self.store.preference('remember_gemini_key'):
            try:
                saved = Keychain(account_for(self.reports)).load()
            except KeychainError as exc:
                errors.append(str(exc))
        if self.store.preference('remember_openai_key'):
            try:
                openai_saved = Keychain(account_for(self.reports)+'.openai').load()
            except KeychainError as exc:
                errors.append(str(exc))
        remember_meta = self.store.preference('remember_meta_key')
        if remember_meta or (self._adopt_keychain and remember_meta is None):
            try:
                meta_saved = Keychain(meta_account(), service=META_SERVICE).load()
                if not meta_saved:
                    meta_saved = Keychain(account_for(self.reports)+'.meta').load()
            except KeychainError as exc:
                errors.append(str(exc))
                meta_saved = ''
        if saved:
            try:
                parsed = json.loads(saved)
                saved_keys = parsed if isinstance(parsed, list) else [saved]
            except (ValueError, TypeError):
                saved_keys = [saved]  # Existing single-key Keychain entry.
            saved_keys = [item for item in saved_keys[:3] if isinstance(item, str) and item]
        else:
            saved_keys = []
        with self.lock:
            if errors and revision == self._credential_revision:
                self.key_storage_error = ' '.join(errors)
            remember_meta = self.store.preference('remember_meta_key')
            if revision == self._credential_revision and (remember_meta or (self._adopt_keychain and remember_meta is None)):
                if not self.meta_key:self.meta_key=meta_saved
                self.meta_key_saved=bool(meta_saved)
                if meta_saved and remember_meta is None:
                    self.store.preference('remember_meta_key',True)
            if revision == self._credential_revision and self.store.preference('remember_openai_key'):
                if not self.openai_key:self.openai_key = openai_saved
                self.openai_key_saved = bool(openai_saved)
            if revision == self._credential_revision and self.store.preference('remember_gemini_key'):
                if not self.key and saved_keys:
                    self.key = saved_keys[0]
                    self.fallback_keys = saved_keys[1:]
                self.key_saved = bool(saved_keys)
                if self.key:
                    self.ai_default = True
            if (self._adopt_keychain and revision == self._credential_revision
                    and self.store.preference('ai_model') is None
                    and self.meta_key and (not self.keys_for_ai() or is_meta(self.model))):
                self.model='meta/'+META_MODEL
                self.store.preference('meta_model',META_MODEL)
                self.store.preference('ai_model',self.model)
            self.keychain_loading = False
            if self.keys_for_ai():self.ai_default = True

    def busy(self):
        return self.uploading or bool(self.job and self.job['status'] == 'running') or bool(
            self.fix_worker and self.fix_worker.is_alive()) or bool(
            self.fix_design_worker and self.fix_design_worker.is_alive()) or any(
            fix['status'] == 'preparing' for fix in self.file_fixes.values()) or any(
            fix['status'] == 'preparing' for fix in self.fix_designs.values()) or bool(
            self.firmware_worker and self.firmware_worker.is_alive()) or any(
            job['status'] == 'preparing' for job in self.firmware_jobs.values())

    def prepare_file_fix(self, data):
        edit_request = data.get('edit_request')
        operations = data.get('operations')
        research = data.get('investigate_design') is True
        if research and (not isinstance(edit_request,str) or not edit_request.strip() or operations is not None):
            raise AppError('Start a design investigation with a repair request, not raw operations.')
        answers = data.get('design_answers',{})
        if not isinstance(answers,dict) or len(answers)>16 or any(not isinstance(k,str) or not isinstance(v,str) or len(k)>1200 or len(v)>2000 for k,v in answers.items()):
            raise AppError('Invalid design answers.')
        batch = edit_request is not None or operations is not None
        if edit_request is not None and (not isinstance(edit_request,str) or not edit_request.strip() or len(edit_request)>2000):
            raise AppError('Describe the requested edit in 2000 characters or fewer.')
        if operations is not None:
            from .board_edits import validate_operations
            try:validate_operations(operations,file_fixes.find_kicad(self.kicad_cli) or '')
            except ValueError as exc:raise AppError(str(exc)) from None
        fix_key = json.dumps([data.get('group_id'), edit_request or '', data.get('design_id') or '',
                                sorted(answers)], sort_keys=True, default=str)
        with self.lock:
            if not self.project or data.get('report_id') != self.report_id:
                raise AppError('Open the current review before preparing an edit.', 409)
            group = next((g for g in self.project.review.get('groups', []) if g['id'] == data.get('group_id')), None)
            if not group:
                raise AppError('Choose a finding from this review.')
            # Enforce the same fail-closed decision as the review UI. A caller
            # must not turn a finding that the current plan classifies as
            # informational/no-change into an edit by posting a custom prompt
            # directly to the API.
            actions = (self.project.action_plan or {}).get('actions', [])
            reviewed_action = next((a for a in actions if a.get('group_id') == group['id']), {})
            from .manufacturing import apply_manufacturing
            current_action = apply_manufacturing({'actions': [{'group_id': group['id']}]},
                                                 {'manufacturing': self.project.manufacturing})['actions'][0]
            if current_action.get('action_kind') == 'order_setting':
                raise AppError('This is a manufacturer order setting, not a KiCad repair. Use the order settings shown on this finding.', 409)
            if (group.get('no_change_basis') or group.get('reference_only') or
                    reviewed_action.get('disposition') == 'no_change' or
                    reviewed_action.get('reference_only')):
                raise AppError('This review does not support a file change for this finding. Update the review after changing the design or its context.', 409)
            project = deepcopy(self.project)
            if research:
                project.design_context=self.store.design_context(context_key(project)) or project.design_context
                inherited=prior_design_answers(self.file_fixes,self.report_id,group['id'],
                                               self.project.provenance.get('source_hashes',{}),self.reports)
                from .workflow import digest
                memory_key=digest([group.get('rule'),group.get('signal_path_id'),group.get('components'),group.get('title')])
                remembered=project.design_context.get('repair_answers',{}).get(memory_key,{})
                answers={**inherited,**remembered,**answers}
            else:
                project.design_context=self.store.design_context(context_key(project)) or project.design_context
            option = file_fixes.availability(self.project, group)
            if option['mode'] != 'automatic' and not batch:
                raise AppError(option['message'], 409)
            key, model = self.keys_for_ai(), self.model
            approved_design=None
            if data.get('design_id'):
                design=self.file_fixes.get(data['design_id'])
                if not design or design.get('status')!='design_approved' or design['report_id']!=self.report_id or design['group_id']!=group['id']:
                    raise AppError('Approve the design approach for this issue first.',409)
                approved_design=deepcopy(design['design'])
            source = (self.store.preference('input:'+self.report_id) or {}).get('path')
            if data.get('design_id'):
                file_fixes.verify_sources(source,design['hashes'])
            fix_key = json.dumps([group['id'], edit_request or '', data.get('design_id') or '',
                                        sorted(answers) if isinstance(answers, dict) else answers], sort_keys=True, default=str)
            for prior in self.file_fixes.values():
                if (isinstance(prior, dict) and prior.get('report_id') == self.report_id
                        and prior.get('fix_key') == fix_key
                        and prior.get('status') in ('preparing', 'ready', 'validated')):
                    # A retry after a timeout or abort reuses the in-flight or
                    # finished preparation instead of forking duplicate work.
                    self.job = {'id': prior['id'], 'kind': 'file_fix', 'status': 'running' if prior['status'] == 'preparing' else 'completed',
                                'message': prior.get('message', ''), 'started': now(), 'events': []}
                    return dict(prior)
            self.idle_required()
            fix_id = secrets.token_hex(16)
            folder = self.reports/'.file-fixes'/fix_id
            record = {'id':fix_id, 'status':'preparing', 'report_id':self.report_id,
                      'group_id':group['id'], 'message':'Preparing a copy. Your project files are unchanged.',
                      'edit_request':edit_request, 'design_id':data.get('design_id'), 'investigate_design':research,
                      'fix_key':fix_key}
            self.file_fixes[fix_id] = record
            self.cancel_event.clear()
            self.job = {'id':fix_id, 'kind':'file_fix', 'status':'running', 'message':record['message'], 'started':now(), 'events':[]}
        usage_mode = self.usage_mode()
        def perform_work():
            # A repair can include datasheet retrieval, a design proposal and
            # one or more evidence-correction calls. Surface a terminal state
            # even if a provider/library call fails to return as expected.
            def expire():
                with self.lock:
                    if record.get('status') != 'preparing' or not self.job or self.job.get('id') != fix_id:
                        return
                    message=('Repair preparation has made no progress for 20 minutes and was marked stalled. '
                             'Your KiCad files were not changed. The current operation will stop when it returns; '
                             'wait until the app is ready before retrying.')
                    record.update(status='blocked', message=message)
                    self.job.update(status='failed', message=message, ended=now())
                    self.cancel_event.set()
                    self.revision += 1
            watchdog = None
            def arm_watchdog():
                nonlocal watchdog
                with self.lock:
                    if record.get('status') != 'preparing':return
                    if watchdog:watchdog.cancel()
                    watchdog = threading.Timer(FILE_FIX_STALL_TIMEOUT_SECONDS, expire)
                    watchdog.daemon = True
                    watchdog.start()
            arm_watchdog()
            try:
                def progress(message):
                    with self.lock:
                        self.progress(message)
                        record['message'] = message
                        arm_watchdog()
                if research:
                    from .repair_design import investigate
                    if not source or not Path(source).is_dir():
                        raise ValueError(fix_source_message(self.store, record['report_id']))
                    hashes=deepcopy(project.provenance.get('source_hashes',{}))
                    if not hashes:raise ValueError('Update the review before planning a repair.')
                    file_fixes.verify_sources(source,hashes)
                    with meta_action('repair'):
                        design=investigate(project,group,edit_request,answers,self.reports/'.repair-research',key,model,progress,source=source)
                    file_fixes.verify_sources(source,hashes)
                    folder.mkdir(parents=True,exist_ok=False)
                    plan={'kind':'design_plan','design_version':9,'status':'awaiting_design_approval','design':design,'design_answers':answers,
                          'source':str(Path(source).resolve()),'hashes':hashes,'summary':design['title'],
                          'message':'Answer the design questions.' if design['questions'] else 'Review the design approach before preparing edits.'}
                elif batch:
                    from .board_edits import prepare
                    plan = prepare(project, group, source, folder, request=edit_request or '', operations=operations,
                                   key=key, model=model, kicad_cli=self.kicad_cli, progress=progress,approved_design=approved_design)
                else:
                    plan = file_fixes.prepare(project, group, source, folder,
                                             kicad_cli=self.kicad_cli, progress=progress)
                if self.cancel_event.is_set():raise JobCancelled()
                with self.lock:
                    if record.get('status') != 'preparing':
                        return
                    record.update(plan)
                    record['providers_used']=getattr(key,'providers_used',[])
                    self.job.update(status='completed',message=plan.get('message','The proposed file edit passed its checks.'),ended=now())
            except JobCancelled:
                with self.lock:
                    if record.get('status') != 'preparing':return
                    record.update(status='blocked',message='Edit preparation cancelled. Your project files were not changed.')
                    self.job.update(status='cancelled',message=record['message'],ended=now())
            except (ValueError, OSError, KeyError, TypeError, StopIteration, subprocess.TimeoutExpired) as exc:
                message=str(exc)
                if 'No space left on device' in message or 'Errno 28' in message:
                    message='Your Mac ran out of storage while preparing the edit preview. The project files were not changed. Free some disk space, then retry the same edit.'
                with self.lock:
                    if record.get('status') != 'preparing':return
                    record.update(status='blocked', message=message)
                    self.job.update(status='failed',message=record['message'],ended=now())
            except Exception as exc:
                incident = self._record_incident('file-fix/prepare worker', exc, method='worker')
                # Never leave a repair in "preparing" if an unexpected planner
                # error escapes the known validation and service failures.
                message=f'Edit preparation stopped because of an internal {type(exc).__name__} error (reference {incident}). Your project files were not changed. Retry once; if it happens again, report the reference.'
                with self.lock:
                    if record.get('status') != 'preparing':return
                    record.update(status='blocked',message=message)
                    self.job.update(status='failed',message=record['message'],ended=now())
            finally:
                if watchdog:watchdog.cancel()
                record['request_usage'] = current_budget().summary()
                try:
                    journal=folder.parent/(folder.name+'-workflow.json')
                    if journal.is_file():
                        history=json.loads(journal.read_text())
                        if isinstance(history,list):record['validation_history']=history[-80:]
                except (OSError,ValueError):
                    pass # Keep the original preparation outcome if the journal cannot be read.
                with self.lock:
                    self.job['request_usage'] = record['request_usage']
                if folder.exists():
                    try:(folder/'plan.json').write_text(json.dumps(record, indent=2))
                    except OSError:pass
                with self.lock:self.revision += 1
        def work():
            with request_budget(usage_mode, 'repair'):
                perform_work()
        self.fix_worker = threading.Thread(target=work, daemon=True)
        self.fix_worker.start()
        return dict(record)

    def continue_file_fix(self,data):
        with self.lock:
            self.idle_required()
            record=self.file_fixes.get(data.get('fix_id'))
            if not record or record.get('kind')!='design_plan' or record.get('status') not in ('awaiting_design_approval','design_approved') or record['report_id']!=self.report_id:
                raise AppError('Open the current design proposal.',409)
            source=(self.store.preference('input:'+self.report_id) or {}).get('path')
            if not source or str(Path(source).resolve())!=record['source']:raise AppError('The connected project changed.',409)
            file_fixes.verify_sources(source,record['hashes'])
            request={'report_id':record['report_id'],'group_id':record['group_id'],'edit_request':record['edit_request']}
            questions=record['design']['questions']
            if data.get('approve_design')==record['id']:
                if record['status']=='awaiting_design_approval':
                    if questions:raise AppError('Answer the design questions before approving the approach.')
                    if record['design'].get('preparation_blocker'):
                        raise AppError(record['design']['preparation_blocker'],409)
                    record['status']='design_approved'
                    (self.reports/'.file-fixes'/record['id']/'plan.json').write_text(json.dumps(record,indent=2))
                request['design_id']=record['id']
            else:
                answers=data.get('answers',{})
                if not isinstance(answers,dict) or set(answers)!={q['id'] for q in questions} or any(not isinstance(v,str) or not v.strip() or len(v)>2000 for v in answers.values()):
                    raise AppError('Answer each question, or enter unknown.')
                from .workflow import digest
                key=context_key(self.project)
                current=self.store.design_context(key) or self.project.design_context or empty_context()
                saved_answers=dict(current.get('question_answers',{}))
                for q in questions:
                    saved_answers[digest(q['question'].casefold().strip())]={'question':q['question'],'answer':answers[q['id']].strip(),
                        'source':'Repair question','report_id':self.report_id,'updated':now()}
                group=next((g for g in self.project.review.get('groups',[]) if g['id']==record['group_id']),None)
                if group is None:raise AppError('The finding changed. Open the current review before answering its repair questions.',409)
                memory_key=digest([group.get('rule'),group.get('signal_path_id'),group.get('components'),group.get('title')])
                repair_answers={**current.get('repair_answers',{}),memory_key:{**record.get('design_answers',{}),**answers}}
                self.store.save_context(key,{**current,'question_answers':saved_answers,'repair_answers':repair_answers},current.get('revision',0))
                self.revision+=1
                request.update(investigate_design=True,design_answers={**record.get('design_answers',{}),**answers})
            try:
                return self.prepare_file_fix(request)
            except Exception:
                if record['status']=='design_approved':
                    record['status']='awaiting_design_approval'
                    (self.reports/'.file-fixes'/record['id']/'plan.json').write_text(json.dumps(record,indent=2))
                raise

    def file_fix(self, fix_id):
        with self.lock:
            if fix_id not in self.file_fixes:
                raise AppError('This edit preview expired. Prepare it again.', 404)
            return deepcopy(self.file_fixes[fix_id])

    def _fix_design_record_path(self, fix_id):
        return self.reports/'.fix-design'/fix_id/'record.json'

    def _persist_fix_design(self, record):
        try:
            self._fix_design_record_path(record['id']).write_text(json.dumps(record, indent=2))
        except OSError:
            pass

    def _record_incident(self, where, exc, method=''):
        """Persist a redacted incident record so a reported reference stays
        resolvable after restarts. Stores only the exception class plus the
        code frames (file, line, function) — never messages, bodies, variable
        values or keys, so a reference can be traced without leaking secrets."""
        incident = secrets.token_hex(4)
        try:
            frames = [{'file': os.path.basename(frame.filename), 'line': frame.lineno,
                       'function': frame.name}
                      for frame in traceback.extract_tb(exc.__traceback__)][-20:]
            folder = self.reports/'.incidents'
            folder.mkdir(parents=True, exist_ok=True)
            (folder/(incident+'.json')).write_text(json.dumps({
                'incident': incident, 'at': now(), 'method': method,
                'path': where, 'exception': type(exc).__name__,
                'frames': frames}, indent=2))
        except OSError:
            pass
        return incident

    def prepare_fix_design(self, data):
        raw_steps = data.get('steps')
        design_key = json.dumps([[e.get('group_id') if isinstance(e, dict) else None,
                                  e.get('operations') if isinstance(e, dict) else None]
                                 for e in raw_steps] if isinstance(raw_steps, list) else raw_steps,
                                sort_keys=True, default=str)
        with self.lock:
            for prior in self.fix_designs.values():
                if (isinstance(prior, dict) and prior.get('report_id') == data.get('report_id')
                        and prior.get('design_key') == design_key
                        and prior.get('status') in ('preparing', 'validated')):
                    # A retry after a timeout or abort reuses the in-flight or
                    # finished preparation instead of forking duplicate work.
                    self.job = {'id': prior['id'], 'kind': 'fix_design', 'status': 'running' if prior['status'] == 'preparing' else 'completed',
                                'message': prior.get('message', ''), 'started': now(), 'events': []}
                    return dict(prior)
            self.idle_required()
            if not self.project or data.get('report_id') != self.report_id:
                raise AppError('Open the current review before preparing a coordinated fix.', 409)
            if not isinstance(raw_steps, list) or not raw_steps or len(raw_steps) > 8:
                raise AppError('Choose one to eight findings for the coordinated fix.')
            actions = {(a.get('group_id')): a for a in (self.project.action_plan or {}).get('actions', [])}
            steps = []
            for entry in raw_steps:
                if not isinstance(entry, dict):
                    raise AppError('Each coordinated-fix step needs a finding.')
                group = next((g for g in self.project.review.get('groups', []) if g['id'] == entry.get('group_id')), None)
                if not group:
                    raise AppError('Choose a finding from this review.')
                reviewed = actions.get(group['id'], {})
                if group.get('no_change_basis') or group.get('reference_only') or \
                        reviewed.get('disposition') == 'no_change' or reviewed.get('reference_only') or \
                        reviewed.get('action_kind') == 'order_setting':
                    raise AppError('This review does not support a file change for finding %s.' % group['id'], 409)
                operations = entry.get('operations')
                if operations is not None:
                    from .board_edits import validate_operations
                    try:
                        validate_operations(operations, file_fixes.find_kicad(self.kicad_cli) or '')
                    except ValueError as exc:
                        raise AppError(str(exc)) from None
                steps.append({'group': deepcopy(group), 'operations': deepcopy(operations) if operations is not None else None,
                              'expect_introduced': []})
            source = (self.store.preference('input:'+self.report_id) or {}).get('path')
            if not source or not Path(source).is_dir():
                raise AppError(fix_source_message(self.store, self.report_id))
            fix_id = secrets.token_hex(16)
            folder = self.reports/'.fix-design'/fix_id
            folder.mkdir(parents=True, exist_ok=False)
            record = {'id': fix_id, 'kind': 'coordinated_fix', 'status': 'preparing',
                      'report_id': self.report_id, 'source': str(Path(source).resolve()),
                      'group_ids': [s['group']['id'] for s in steps], 'design_key': design_key,
                      'message': 'Preparing the coordinated fix on an isolated copy. Your files are unchanged.'}
            self.fix_designs[fix_id] = record
            self._persist_fix_design(record)
            self.job = {'id': fix_id, 'kind': 'fix_design', 'status': 'running',
                        'message': record['message'], 'started': now(), 'events': []}
        usage_mode = self.usage_mode()
        def perform_work():
            try:
                def progress(message):
                    with self.lock:
                        self.progress(message)
                        record['message'] = message
                with request_budget(usage_mode, 'repair'):
                    project = deepcopy(self.project)
                    result = fix_plan.run_plan(
                        source, project, steps, folder/'workspace',
                        kicad_cli=self.kicad_cli, progress=progress)
                if self.cancel_event.is_set():
                    raise JobCancelled()
                with self.lock:
                    if record.get('status') != 'preparing':
                        return
                    record.update(status='validated', candidate=result['candidate'],
                                  steps=result['steps'], final=result['final'],
                                  files_changed=sorted(r for r, d in result['diff'].items() if d['changed']),
                                  diffs={r: (d['diff'] or '')[:4000] for r, d in result['diff'].items() if d['changed']},
                                  message='The coordinated fix passed all checks on an isolated copy. Review and approve it before exporting.')
                    self.job.update(status='completed', message=record['message'], ended=now())
            except JobCancelled:
                with self.lock:
                    if record.get('status') != 'preparing':
                        return
                    record.update(status='blocked', message='Coordinated-fix preparation was cancelled. Your project files were not changed.')
                    self.job.update(status='cancelled', message=record['message'], ended=now())
            except (ValueError, OSError) as exc:
                with self.lock:
                    if record.get('status') != 'preparing':
                        return
                    record.update(status='blocked', message=str(exc)[:500] or 'Preparation failed. Your project files were not changed.')
                    self.job.update(status='failed', message=record['message'], ended=now())
            except Exception as exc:
                incident = self._record_incident('fix-design/prepare worker', exc, method='worker')
                with self.lock:
                    if record.get('status') != 'preparing':
                        return
                    record.update(status='blocked',
                                  message='Preparation stopped because of an internal %s error (reference %s). Your project files were not changed. If it persists, report the reference.' % (type(exc).__name__, incident))
                    self.job.update(status='failed', message=record['message'], ended=now())
            finally:
                record['request_usage'] = current_budget().summary()
                self._persist_fix_design(record)
                with self.lock:
                    self.revision += 1
        def work():
            with request_budget(usage_mode, 'repair'):
                perform_work()
        self.fix_design_worker = threading.Thread(target=work, daemon=True)
        self.fix_design_worker.start()
        return dict(record)

    def reconnect_fix_design(self, data):
        """Re-bind a saved report to its KiCad folder after the user picks it again.

        Used when prepare fails with 'Connect the saved KiCad folder first.'
        (upload-based reports, moved folders, unmounted volumes). The folder is
        accepted only when every tracked file is present with a matching hash;
        nothing is edited.
        """
        with self.lock:
            self.idle_required()
            report_id = data.get('report_id', '')
            report = self.read_report(report_id)
            saved = (report.get('provenance') or {}).get('source_hashes') or {}
            if isinstance(data.get('folder'), str) and data['folder'].strip():
                folder = Path(data['folder'].strip()).expanduser().resolve()
                if not folder.is_dir():
                    raise AppError('That folder was not found. Choose the folder you open in KiCad.')
                names = [n for n in saved if Path(n).suffix in ('.kicad_sch', '.kicad_pcb')]
                if not names:
                    raise AppError('This report has no saved file record, so the folder cannot be verified. Run the analysis again.')
                sch = next((n for n in names if n.endswith('.kicad_sch')), None)
                pcb = next((n for n in names if n.endswith('.kicad_pcb')), None)
                pro = (Path(pcb).stem + '.kicad_pro') if pcb else None
                if not sch or not pcb or not pro:
                    raise AppError('This report has no saved file record, so the folder cannot be verified. Run the analysis again.')
                paths = [str(folder / sch), str(folder / pcb), str(folder / pro)]
            elif isinstance(data.get('paths'), list):
                paths = data['paths']
            else:
                raise AppError('Choose the project folder you open in KiCad.')
            linked = self.link_local_files(paths, watch=False)
            folder = linked['path']
            if not saved:
                raise AppError('This report has no saved file record, so the folder cannot be verified. Run the analysis again.')
            missing, changed = [], []
            for name, expected in saved.items():
                try:
                    actual = file_fixes.digest(file_fixes.source_file(folder, name).read_bytes())
                except OSError:
                    missing.append(name)
                    continue
                if actual != expected:
                    changed.append(name)
            if missing:
                raise AppError('The folder is missing: %s. Choose the folder with the analyzed files.' % ', '.join(missing[:4]))
            if changed:
                raise AppError('These files changed since the analysis: %s. Re-run the analysis before preparing a fix.' % ', '.join(changed[:4]))
            self.store.preference('input:' + report_id, {'path': str(folder)})
            return {'path': str(folder), 'files': sorted(saved)}

    def fix_design(self, fix_id):
        with self.lock:
            if fix_id not in self.fix_designs:
                raise AppError('This coordinated fix expired. Prepare it again.', 404)
            return deepcopy(self.fix_designs[fix_id])

    def list_fix_designs(self, report_id):
        with self.lock:
            if not report_id or report_id != self.report_id:
                raise AppError('Open the review to see its coordinated fixes.', 404)
            items = [{'id': fix_id, 'status': record.get('status'),
                      'group_ids': list(record.get('group_ids') or []),
                      'files_changed': list(record.get('files_changed') or []),
                      'message': (record.get('message') or '')[:500]}
                     for fix_id, record in self.fix_designs.items()
                     if record.get('report_id') == report_id]
            items.reverse()
            return {'fixes': items}

    def _fix_design_candidate(self, record):
        if not record or record.get('report_id') != self.report_id:
            raise AppError('Open the review used to prepare this coordinated fix.', 409)
        source = (self.store.preference('input:'+self.report_id) or {}).get('path')
        if not source or str(Path(source).resolve()) != record.get('source'):
            raise AppError('The connected project changed. Prepare a new coordinated fix.', 409)
        return self.reports/'.fix-design'/record['id']/'workspace', record['candidate']

    def approve_fix_design(self, data):
        with self.lock:
            self.idle_required()
            record = self.fix_designs.get(data.get('fix_id'))
            if not record or record.get('status') != 'validated':
                raise AppError('Review the validated preview before approving.', 409)
            wsroot, cx = self._fix_design_candidate(record)
            try:
                decision = fix_decision.approve(wsroot, cx, by='review-ui')
            except (ValueError, OSError) as exc:
                raise AppError(str(exc), 409) from None
            record.update(status='approved', decision=decision,
                          message='Approved. Export the corrected project; the original is unchanged.')
            self._persist_fix_design(record)
            self.revision += 1
            return dict(record)

    def reject_fix_design(self, data):
        with self.lock:
            self.idle_required()
            record = self.fix_designs.get(data.get('fix_id'))
            if not record or record.get('status') not in ('validated', 'blocked'):
                raise AppError('There is no pending coordinated fix to reject.', 409)
            reason = data.get('reason', '')
            if not isinstance(reason, str) or len(reason) > 500:
                raise AppError('Keep the rejection reason under 500 characters.')
            wsroot = self.reports/'.fix-design'/record['id']/'workspace'
            if record.get('status') == 'validated':
                source = (self.store.preference('input:'+self.report_id) or {}).get('path')
                if not source or str(Path(source).resolve()) != record.get('source'):
                    raise AppError('The connected project changed. Prepare a new coordinated fix.', 409)
                try:
                    fix_decision.reject(wsroot, record['candidate'], reason)
                except (ValueError, OSError) as exc:
                    raise AppError(str(exc), 409) from None
            record.update(status='rejected', message='Rejected. The candidate was restored; the original was never changed.')
            self._persist_fix_design(record)
            self.revision += 1
            return {'status': 'rejected'}

    def export_fix_design(self, fix_id):
        with self.lock:
            record = self.fix_designs.get(fix_id)
            if not record or record.get('status') != 'approved':
                raise AppError('Approve the coordinated fix before exporting.', 409)
            wsroot, cx = self._fix_design_candidate(record)
            dest = self.reports/'.fix-design'/fix_id/'export'
            try:
                if dest.exists():
                    shutil.rmtree(dest)
                result = fix_decision.export_approved(wsroot, cx, dest)
            except (ValueError, OSError) as exc:
                raise AppError(str(exc), 409) from None
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
                for path in sorted(dest.rglob('*')):
                    if path.is_file():
                        bundle.write(path, path.relative_to(dest).as_posix())
            record.update(status='exported', message='Exported the corrected project. The original is unchanged.')
            self._persist_fix_design(record)
            self.revision += 1
            return archive.getvalue(), result['files']

    def _firmware_record_path(self, job_id):
        return self.reports/'.firmware'/job_id/'record.json'

    @staticmethod
    def _seeded_firmware_answers(jobs, report_id, job_id):
        """Answers carried into a reopened discovery on the same review.

        Unions well-formed answers across every validated same-report
        build, latest answer per need winning (dict insertion order is
        recency order), so answers scattered across builds reunite instead
        of re-asking. The caller filters them against the fresh spec's
        open needs, so hardware changes cannot resurrect stale answers.
        """
        merged = {}
        for prior in (jobs or {}).values():
            if (isinstance(prior, dict) and prior.get('report_id') == report_id
                    and prior.get('status') == 'validated' and prior.get('answers')
                    and prior.get('id') != job_id):
                for answer in prior['answers']:
                    if isinstance(answer, dict) and answer.get('need') and answer.get('answer'):
                        merged[answer['need']] = answer
        return list(merged.values())

    def _persist_firmware(self, record):
        try:
            self._firmware_record_path(record['id']).write_text(json.dumps(record, indent=2))
        except OSError:
            pass

    def prepare_firmware(self, data):
        with self.lock:
            self.idle_required()
            if not self.project or data.get('report_id') != self.report_id:
                raise AppError('Open the current review before creating firmware.', 409)
            qc_snapshot = self._firmware_prior()
            job_id = secrets.token_hex(16)
            folder = self.reports/'.firmware'/job_id
            folder.mkdir(parents=True, exist_ok=False)
            record = {'id': job_id, 'kind': 'firmware_discovery', 'status': 'preparing',
                      'report_id': self.report_id,
                      'message': 'Reading the reviewed hardware. Your project files are unchanged.'}
            self.firmware_jobs[job_id] = record
            self._persist_firmware(record)
            self.job = {'id': job_id, 'kind': 'firmware', 'status': 'running',
                        'message': record['message'], 'started': now(), 'events': []}
        def perform_work():
            try:
                with self.lock:
                    project = deepcopy(self.project)
                    # Reopening discovery on the same review keeps previously
                    # saved answers: seed from the latest validated same-report
                    # build so a new job resumes where the designer left off.
                    seeded = self._seeded_firmware_answers(self.firmware_jobs, record.get('report_id'),
                                                             record.get('id'))
                spec = build_firmware_spec(asdict(project), extras_from_answers(seeded))
                open_needs = {q.get('need') for q in spec.get('open_questions') or []
                              if isinstance(q, dict) and q.get('need')}
                seeded = [a for a in seeded if a.get('need') in open_needs]
                prior_answers = dict(qc_snapshot['answers'])
                prior_answers.update({a['need']: a['answer'] for a in seeded})
                questions = route_questions(spec, prior_answers=prior_answers,
                                            documented=qc_snapshot['documented'])
                with self.lock:
                    if record.get('status') != 'preparing':
                        return
                    record.update(status='validated', spec=spec, questions=questions, answers=seeded,
                                  message='Hardware discovery finished. Review the MCU, connections, and open questions before planning firmware.')
                    self.job.update(status='completed', message=record['message'], ended=now())
            except (ValueError, OSError) as exc:
                with self.lock:
                    if record.get('status') != 'preparing':
                        return
                    record.update(status='blocked', message=str(exc)[:500] or 'Firmware discovery failed.')
                    self.job.update(status='failed', message=record['message'], ended=now())
            except Exception as exc:
                with self.lock:
                    if record.get('status') != 'preparing':
                        return
                    record.update(status='blocked',
                                  message='Discovery stopped because of an internal %s error.' % type(exc).__name__)
                    self.job.update(status='failed', message=record['message'], ended=now())
            finally:
                self._persist_firmware(record)
                with self.lock:
                    self.revision += 1
        self.firmware_worker = threading.Thread(target=perform_work, daemon=True)
        self.firmware_worker.start()
        return dict(record)

    def firmware(self, job_id):
        with self.lock:
            if job_id not in self.firmware_jobs:
                raise AppError('This firmware discovery expired. Create the firmware again.', 404)
            return deepcopy(self.firmware_jobs[job_id])

    def _firmware_prior(self):
        """Reviewer answers and documented intent for firmware question routing."""
        snapshot = {'answers': {}, 'documented': []}
        try:
            current = self.store.design_context(context_key(self.project)) or {}
            for rec in (current.get('question_answers') or {}).values():
                if isinstance(rec, dict) and rec.get('question') and rec.get('answer'):
                    snapshot['answers'][rec['question']] = rec['answer']
            if isinstance(current.get('description'), str) and current['description'].strip():
                snapshot['documented'].append(('description', current['description'].strip()[:500]))
            for intent in current.get('intentions', []) or []:
                if isinstance(intent, dict) and str(intent.get('note', '')).strip():
                    snapshot['documented'].append(
                        ('intention:%s' % intent.get('group_id', '?'),
                         str(intent['note']).strip()[:500]))
            operating = current.get('operating_context', {}) or {}
            if isinstance(operating, dict):
                for key, val in operating.items():
                    if isinstance(val, str) and val.strip():
                        snapshot['documented'].append(('operating:%s' % key, val.strip()[:300]))
        except (OSError, ValueError, AttributeError):
            pass
        return snapshot

    def plan_firmware(self, data):
        with self.lock:
            record = self.firmware_jobs.get(data.get('job_id'))
            if not record or record.get('report_id') != self.report_id:
                raise AppError('Open the review used for this firmware discovery.', 409)
            if record.get('status') != 'validated' or 'spec' not in record:
                raise AppError('Run firmware discovery before planning firmware.', 409)
            spec = deepcopy(record['spec'])
            mode = self.usage_mode()
            key = self.meta_key
        result = build_firmware_plan(spec, answers=record.get('answers'))
        if not result.get('ok'):
            raise AppError(result.get('reason') or 'Firmware planning is blocked.', 409)
        # The Meta review annotates the deterministic plan; it never blocks
        # or replaces it, and it runs outside the lock.
        plan = review_plan_with_meta(result['plan'], spec, key=key, mode=mode)
        with self.lock:
            record = self.firmware_jobs.get(data.get('job_id'))
            if not record or record.get('report_id') != self.report_id:
                raise AppError('Open the review used for this firmware discovery.', 409)
            if record.get('status') != 'validated' or 'spec' not in record:
                raise AppError('Run firmware discovery before planning firmware.', 409)
            if record.get('spec') != spec:
                # Designer answers landed mid-review; rebuild deterministically
                # from the current spec instead of annotating a stale one.
                fresh = build_firmware_plan(record['spec'], answers=record.get('answers'))
                if not fresh.get('ok'):
                    raise AppError(fresh.get('reason') or 'Firmware planning is blocked.', 409)
                plan = fresh['plan']
            record.update(plan=plan,
                          message='Firmware plan ready. Review the framework, drivers, and decisions.')
            self._persist_firmware(record)
            self.revision += 1
            return deepcopy(record)

    def generate_firmware(self, data):
        with self.lock:
            record = self.firmware_jobs.get(data.get('job_id'))
            if not record or record.get('report_id') != self.report_id:
                raise AppError('Open the review used for this firmware discovery.', 409)
            if record.get('status') != 'validated' or 'spec' not in record:
                raise AppError('Run firmware discovery before generating.', 409)
            if 'plan' not in record:
                raise AppError('Plan firmware before generating the project.', 409)
            result = build_firmware_project(record['spec'], record['plan'])
            if not result.get('ok'):
                return {'ok': False, 'reason': result.get('reason', 'Generation is blocked.')}
            build = {'files': result['files'], 'validation': result['validation'],
                     'message': result.get('message', ''), 'target': result.get('target', {}),
                     'stats': result.get('stats', {})}
            record.update(build=build,
                          message='Firmware project generated. Compile it with make (Milestone E).')
            self._persist_firmware(record)
            self.revision += 1
            return deepcopy(record)

    def compile_firmware(self, data):
        from .firmware.compile import verify_build
        from .firmware.generator import generate_firmware as build_firmware_project
        with self.lock:
            record = self.firmware_jobs.get(data.get('job_id'))
            if not record or record.get('report_id') != self.report_id:
                raise AppError('Open the review used for this firmware discovery.', 409)
            if record.get('status') != 'validated' or 'build' not in record:
                raise AppError('Generate the firmware project before compiling.', 409)
            files = dict(record['build'].get('files') or {})
            spec = deepcopy(record['spec'])
            plan = deepcopy(record.get('plan') or {})
            job_id = record['id']

        def regen(modules_to_add):
            merged = dict(plan)
            merged['modules'] = sorted(set(plan.get('modules', []) or []) | set(modules_to_add or []))
            out = build_firmware_project(spec, merged)
            return out.get('files', {}) if out.get('ok') else {}

        build_dir = self.reports/'.firmware'/job_id/'build'
        try:
            build_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AppError('Could not save the compiled project: %s' % exc)
        report = verify_build(files, regen=regen, max_attempts=3,
                              artifact_dir=str(build_dir))
        with self.lock:
            record = self.firmware_jobs.get(job_id)
            if not record or record.get('report_id') != self.report_id:
                raise AppError('Open the review used for this firmware discovery.', 409)
            if record.get('status') != 'validated' or 'build' not in record:
                raise AppError('Generate the firmware project before compiling.', 409)
            try:
                for name, content in files.items():
                    if not isinstance(name, str) or not isinstance(content, str):
                        continue
                    if ".." in name.split("/"):
                        continue
                    target = build_dir/name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content)
                (build_dir/'compile.json').write_text(json.dumps(report, indent=2)[:65536])
            except OSError as exc:
                raise AppError('Could not save the compiled project: %s' % exc)
            if isinstance(report.get('files'), dict) and report['files']:
                record['build']['files'] = report['files']
            stored = {k: v for k, v in report.items() if k != 'files'}
            record['build']['compile'] = stored
            record.update(message='Firmware compile %s. See the build checks below.'
                          % report.get('status', 'finished'))
            self._persist_firmware(record)
            self.revision += 1
            return deepcopy(record)

    def firmware_artifact(self, data):
        with self.lock:
            record = self.firmware_jobs.get(data.get('job_id'))
            if not record or record.get('report_id') != self.report_id:
                raise AppError('Open the review used for this firmware discovery.', 409)
            job_id = record['id']
            allowed = {'firmware.uf2': 'application/octet-stream',
                       'firmware.elf': 'application/octet-stream',
                       'firmware.hex': 'text/plain',
                       'main.c': 'text/plain; charset=utf-8',
                       'pins.h': 'text/plain; charset=utf-8',
                       'hal_i2c.c': 'text/plain; charset=utf-8',
                       'hal_i2c.h': 'text/plain; charset=utf-8',
                       'CMakeLists.txt': 'text/plain; charset=utf-8',
                       'Makefile': 'text/plain; charset=utf-8',
                       'README.md': 'text/plain; charset=utf-8',
                       'firmware.json': 'application/json; charset=utf-8',
                       'compile.json': 'application/json; charset=utf-8'}
            name = data.get('name', '')
            if name not in allowed:
                raise AppError('That build file is not available for download.', 404)
            target = self.reports/'.firmware'/job_id/'build'/name
        try:
            body = target.read_bytes()
        except OSError:
            raise AppError('That build file has not been saved yet. Compile first.', 404)
        if not body:
            raise AppError('That build file is empty.', 404)
        return body, allowed[name], name

    def answer_firmware(self, data):
        from .ai.question_creator import route_answer
        with self.lock:
            record = self.firmware_jobs.get(data.get('job_id'))
            if not record or record.get('report_id') != self.report_id:
                raise AppError('Open the review used for this firmware discovery.', 409)
            if record.get('status') != 'validated' or 'spec' not in record:
                raise AppError('Run firmware discovery before answering.', 409)
            need = data.get('need', '')
            answer = data.get('answer', '')
            mpn = data.get('mpn', '')
            if not isinstance(need, str) or not isinstance(answer, str) or not isinstance(mpn, str):
                raise AppError('Enter a question and answer.')
            need, answer, mpn = need.strip(), answer.strip(), mpn.strip()
            if not need or not answer or len(need) > 2000 or len(answer) > 2000 or len(mpn) > 120:
                raise AppError('Enter an answer under 2,000 characters (part number under 120).')
            question = next((q for q in record.get('questions', [])
                             if q.get('need') == need and q.get('status') == 'user'), None)
            if question is None:
                raise AppError('This question is no longer open.', 409)
            if re.fullmatch(r"(i\s+)?(am\s+)?(not sure|don't know|do not know|unknown|not yet|skip|n/a)[.!\s]*",
                             answer.casefold()):
                question.update(status='declined', answer=answer)
                self._persist_firmware(record)
                self.revision += 1
                return deepcopy(record)
            try:
                receipt = route_answer(question.get('asked_decision') or {}, answer)
            except ValueError as exc:
                raise AppError(str(exc))
            project = deepcopy(self.project)
            snapshot = self._firmware_prior()
            stored = {'need': need, 'resolves': question.get('resolves', ''),
                      'scope': question.get('scope', {}), 'answer': answer,
                      'mpn': mpn if question.get('resolves') in ('mcu', 'peripheral') else '',
                      'receipt': receipt, 'at': now()}
            answers = [a for a in record.get('answers', []) if a.get('need') != need] + [stored]
            prior = dict(snapshot['answers'])
            prior.update({a['need']: a['answer'] for a in answers if a.get('answer')})
            spec = build_firmware_spec(asdict(project), extras_from_answers(answers))
            questions = route_questions(spec, prior_answers=prior,
                                        documented=snapshot['documented'])
            record.update(spec=spec, questions=questions, answers=answers,
                          message='Answer saved. Discovery now reflects it; plan and generate again.')
            record.pop('plan', None)
            record.pop('build', None)
            self._persist_firmware(record)
            self.revision += 1
            return deepcopy(record)

    def list_firmware_builds(self, report_id):
        with self.lock:
            if not report_id or report_id != self.report_id:
                raise AppError('Open the review to see its firmware discoveries.', 404)
            items = [{'id': job_id, 'status': record.get('status'),
                      'message': (record.get('message') or '')[:500]}
                     for job_id, record in self.firmware_jobs.items()
                     if record.get('report_id') == report_id]
            items.reverse()
            return {'builds': items}

    def export_firmware(self, job_id):
        with self.lock:
            record = self.firmware_jobs.get(job_id)
            if not record:
                raise AppError('This firmware discovery expired. Create the firmware again.', 404)
            files = (record.get('build') or {}).get('files') or {}
            if not files:
                raise AppError('Generate the firmware before exporting it.', 409)
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
                for name in sorted(files):
                    bundle.writestr(name, files[name])
            self.revision += 1
            return archive.getvalue(), 'preflight-firmware-%s.zip' % job_id[:8]

    def apply_file_fix(self, data, undo=False):
        with self.lock:
            self.idle_required()
            record = self.file_fixes.get(data.get('fix_id'))
            if not record or record['report_id'] != self.report_id:
                raise AppError('Open the review used to prepare this edit.', 409)
            source = (self.store.preference('input:'+self.report_id) or {}).get('path')
            if not source or str(Path(source).resolve()) != record.get('source'):
                raise AppError('The connected project changed. Prepare a new edit.', 409)
            if data.get('closed_in_kicad') is not True:
                raise AppError('Save and close this board in KiCad before restoring the backup.'
                               if undo else
                               'Save and close this board in KiCad before applying the edit.')
            if record.get('requires_approval') and data.get('approve_plan') != record['id']:
                raise AppError('Review and approve every operation in this edit preview before applying it.')
            try:
                folder = self.reports/'.file-fixes'/record['id']
                result = file_fixes.apply(record, folder, undo=undo)
            except (ValueError, OSError) as exc:
                raise AppError(str(exc), 409) from None
            (folder/'plan.json').write_text(json.dumps(record, indent=2))
            if self.job and self.job.get('id') == record['id']:
                self.job.update(status='completed', message=result['message'], ended=now())
            self.revision += 1
            return result

    def idle_required(self):
        if self.busy():
            raise AppError('A check is already running. Wait for it to finish.', 409)

    def config(self):
        with self.lock:
            return {'default_path': self.default_path, 'key_configured': bool(self.keys_for_ai()),
                    'configured_keys': (int(bool(self.meta_key)) if is_meta(self.model) else int(bool(self.openai_key)) if self.model=='gpt-6-luna' else len(self.gemini_keys())),
                    'key_saved': self.meta_key_saved if is_meta(self.model) else self.openai_key_saved if self.model=='gpt-6-luna' else self.key_saved,
                    'meta_key_configured':bool(self.meta_key), 'meta_key_saved':self.meta_key_saved,
                    'meta_model':self.store.preference('meta_model') or META_MODEL,
                    'openai_key_configured': bool(self.openai_key), 'openai_key_saved': self.openai_key_saved,
                    'gemini_key_configured': bool(self.key), 'gemini_key_saved':self.key_saved,
                    'gemini_backup_enabled':self.gemini_backup_enabled, 'gemini_model':self.gemini_model,
                    'provider':'Meta' if is_meta(self.model) else 'OpenAI' if self.model=='gpt-6-luna' else 'Gemini',
                    'keychain_available': keychain_available(), 'key_storage_error': self.key_storage_error,
                    'keychain_loading': self.keychain_loading,
                    'connection_test': ({k:v for k,v in self.connection_test.items() if k != 'credential_revision'}
                        if self.connection_test and self.connection_test['credential_revision']==self._credential_revision
                        and self.connection_test['model']==self.model else None),
                    'model': self.model, 'csrf_token': self.token, 'server_id': self.server_id,
                    'max_upload_bytes': MAX_ARCHIVE_BYTES,
                    'app_version': APP_VERSION,
                    'ai_default': self.ai_default, 'native_default': self.native_default,
                    'ai_usage_mode': self.usage_mode(),
                    'ai_request_limits': LIMITS[self.usage_mode()],
                    'linked_files': self.store.preference('linked_kicad_files'),
                    'usage': self.usage_summary()}

    def keys_for_ai(self):
        if is_meta(self.model):return self.meta_key
        return self.gemini_keys()

    def _default_model(self):
        """Meta-first default that never strands a configured key on another provider."""
        if self.meta_key or not (self.key or self.openai_key
                or self.store.preference('remember_gemini_key')
                or self.store.preference('remember_openai_key')):
            return PROVIDERS['meta']['model']
        if self.openai_key or self.store.preference('remember_openai_key'):
            return 'gpt-6-luna'
        return self.gemini_model

    def usage_budget(self):
        budget = self.store.preference('meta_budget_usd')
        warn = self.store.preference('meta_warn_usd')
        warn_high = self.store.preference('meta_warn_high_usd')
        if not isinstance(budget, (int, float)) or isinstance(budget, bool) or budget < 0:
            budget = 75.0
        if not isinstance(warn, (int, float)) or isinstance(warn, bool) or warn < 0:
            warn = 60.0
        if not isinstance(warn_high, (int, float)) or isinstance(warn_high, bool) or warn_high < 0:
            warn_high = 70.0
        return float(budget), float(warn), float(warn_high)

    def usage_summary(self):
        """Serve the last good tracker state when storage is briefly unavailable."""
        with self.lock:
            try:
                budget, warn, warn_high = self.usage_budget()
                summary = self.store.meta_usage_summary(
                    budget_usd=budget, warn_usd=warn, warn_high_usd=warn_high,
                    model='meta/' + META_MODEL, pricing=META_PRICING)
            except sqlite3.Error:
                return self._usage_cache
            self._usage_cache = summary
            return summary

    def save_usage_budget(self, data):
        values = {}
        for field in ('budget_usd', 'warn_usd', 'warn_high_usd'):
            value = data.get(field)
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or value < 0 or value > 1000000):
                raise AppError('Enter budget and warning amounts as numbers from 0 to 1000000.')
            values[field] = float(value)
        if not values['warn_usd'] <= values['warn_high_usd'] <= values['budget_usd']:
            raise AppError('Keep the warnings ordered: first warning, then second warning, then budget.')
        with self.lock:
            self.store.preference('meta_budget_usd', values['budget_usd'])
            self.store.preference('meta_warn_usd', values['warn_usd'])
            self.store.preference('meta_warn_high_usd', values['warn_high_usd'])
            return self.usage_summary()

    def editor_status(self):
        result = self.editor_connections.listing()
        with self.lock:
            source = (self.store.preference('input:'+self.report_id) or {}).get('path') if self.report_id else None
            hashes = dict(self.project.provenance.get('source_hashes', {})) if self.project else {}
        for record in result['connections']:
            record['review_available'] = EDITORS[record['editor']]['review_available']
            record['current_report'] = False
            record['review_outdated'] = None
            if source and Path(source).is_dir() and record['editor'] == 'kicad':
                pro = next((Path(p) for p in record['files'] if p.endswith('.kicad_pro')), None)
                if pro and pro.parent == Path(source).resolve():
                    record['current_report'] = True
                    try:
                        saved = self.editor_connections.get(record['id'])['hashes']
                    except EditorConnectionError:
                        continue  # Disconnected in another browser tab.
                    compared = [(str(Path(p).relative_to(pro.parent)), digest) for p, digest in saved.items()
                                if str(Path(p).relative_to(pro.parent)) in hashes]
                    record['review_outdated'] = any(hashes[name] != digest for name, digest in compared) if compared else None
        return result

    def connect_editor(self, data):
        try:
            return self.editor_connections.connect(data.get('editor'), data.get('files'))
        except (OSError, ValueError) as exc:
            raise AppError(str(exc)) from None

    def use_editor_connection(self, identifier):
        try:
            record = self.editor_connections.get(identifier)
        except EditorConnectionError as exc:
            raise AppError(str(exc), 404) from None
        if record['editor'] != 'kicad':
            raise AppError('Saved-file tracking is connected, but Preflight cannot analyze this native format yet. Convert a copy to KiCad for review; that copy will not stay synchronized with this editor.', 409)
        files = [Path(p) for p in record['files']]
        pro = next(p for p in files if p.suffix == '.kicad_pro')
        pcb = next(p for p in files if p.suffix == '.kicad_pcb')
        sheets = [p for p in files if p.suffix == '.kicad_sch']
        return self.link_local_files([str(pro), str(pcb), *[str(sheet) for sheet in sheets]], watch=False)

    def gemini_keys(self):
        return [value for value in [self.key, *self.fallback_keys] if isinstance(value,str) and value]

    def link_local_files(self, paths, *, watch=True):
        if not isinstance(paths, list) or not paths:
            raise AppError('Select the schematic (.kicad_sch) sheets, one board (.kicad_pcb), and one project (.kicad_pro).')
        by_suffix = {}
        for raw in paths:
            if not isinstance(raw, str) or '\n' in raw or '\x00' in raw:
                raise AppError('Invalid file path.')
            file = Path(raw).expanduser().resolve()
            if not file.is_file() or file.suffix not in ('.kicad_sch', '.kicad_pcb', '.kicad_pro'):
                raise AppError('Select .kicad_sch sheets, one .kicad_pcb, and one .kicad_pro file.')
            by_suffix.setdefault(file.suffix, []).append(file)
        pro, pcb, sheets = by_suffix.get('.kicad_pro', []), by_suffix.get('.kicad_pcb', []), by_suffix.get('.kicad_sch', [])
        if len(pro) != 1 or len(pcb) != 1 or not sheets:
            raise AppError('Select the schematic (.kicad_sch) sheets, one board (.kicad_pcb), and one project (.kicad_pro).')
        if len(sheets) > 64:
            raise AppError('More than 64 schematic sheets were selected; link at most 64.')
        files = pro + pcb + sheets
        if len({file.parent for file in files}) != 1:
            raise AppError('Choose the schematic sheets, board, and project from the same KiCad project folder.')
        if pcb[0].stem != pro[0].stem:
            raise AppError('The PCB and project file must have the same name so KiCad uses the selected project settings.')
        entries = []
        for sheet in sorted(sheets, key=lambda item: item.name):
            try:
                entries.append((sheet.name, sheet.read_text(encoding='utf-8-sig')))
            except (OSError, UnicodeError) as exc:
                raise AppError(f'Could not read {sheet.name}: {exc}') from None
        schematic = resolve_root_schematic(entries, pro[0].stem)
        sheet_names = sorted(sheet.name for sheet in sheets)
        linked = {'path': str(pro[0].parent), 'name': pro[0].parent.name or pro[0].stem,
                  'schematic': schematic, 'pcb': pcb[0].name, 'sheets': sheet_names,
                  'files': {'.kicad_pro': str(pro[0]), '.kicad_pcb': str(pcb[0]),
                            '.kicad_sch': str(pro[0].parent / schematic)}}
        with self.lock:
            self.idle_required()
        if watch:
            self.connect_editor({'editor': 'kicad', 'files': [str(file) for file in files]})
        with self.lock:
            self.idle_required()
            self.store.preference('linked_kicad_files', linked)
        return linked

    def link_local_names(self, directory, names=None):
        """Link staged file names against a previously confirmed folder.

        The browser never reveals local paths for dropped files, so the page
        cannot link a drop on its own. But once the user has confirmed a
        project folder (native dialog, typed path, or an earlier link), the
        saved route lets a later drop or Link press resolve instantly with no
        new dialog. Raises AppError when the route is unknown or the files
        are not all in that folder; callers fall back to the native picker.
        """
        if not directory:
            saved = self.store.preference('linked_kicad_files') or {}
            directory = saved.get('path')
        if not directory or not isinstance(directory, str):
            raise AppError('Confirm the project folder first (Browse or folder path), then drop the files.')
        folder = Path(directory).expanduser()
        if not folder.is_dir():
            raise AppError(f'The saved project folder is no longer there: {directory}')
        if names is None:
            names = [item.name for item in sorted(folder.glob('*.kicad_pro'))]
            names += [item.name for item in sorted(folder.glob('*.kicad_pcb'))]
            names += [item.name for item in sorted(folder.glob('*.kicad_sch'))]
        if not isinstance(names, list) or not names:
            raise AppError('No file names to link. Drop the KiCad files first.')
        for name in names:
            if not isinstance(name, str) or not name or '/' in name or '\\' in name or name.startswith('.'):
                raise AppError(f'Invalid file name: {name}')
        missing = [name for name in names if not (folder / name).is_file()]
        if missing:
            raise AppError('These files are not in %s: %s. Drop the files from that folder, or type the folder they are in.' % (directory, ', '.join(missing[:8])))
        return self.link_local_files([str(folder / name) for name in names])

    def demo_projects(self):
        """Bundled judge-ready demo projects, no upload or external drive needed.

        Only subdirectories of the repo's own demo-projects folder that hold
        one .kicad_pro, one .kicad_pcb and at least one .kicad_sch are listed.
        Directories come from the bundled folder itself, so callers cannot
        redirect this listing at arbitrary paths.
        """
        root = Path(__file__).resolve().parent / 'demo-projects'
        demos = []
        if root.is_dir():
            for folder in sorted(root.iterdir(), key=lambda item: item.name):
                if not folder.is_dir() or folder.name.startswith('.'):
                    continue
                pro = sorted(folder.glob('*.kicad_pro'))
                pcb = sorted(folder.glob('*.kicad_pcb'))
                sch = sorted(folder.glob('*.kicad_sch'))
                if len(pro) == 1 and len(pcb) == 1 and sch:
                    preset_file = folder / 'preset-context.md'
                    try:
                        preset = preset_file.read_text(encoding='utf-8-sig')[:6000] if preset_file.is_file() else ''
                    except (OSError, UnicodeError):
                        preset = ''
                    demos.append({'name': folder.name, 'directory': str(folder),
                                  'files': [pro[0].name, pcb[0].name]
                                           + sorted(item.name for item in sch),
                                  'preset': preset})
        return {'demos': demos}

    def _demo_directory(self, name):
        """Resolve a demo name to its bundled folder; client paths are never trusted."""
        if not isinstance(name, str):
            raise AppError('Unknown demo project.')
        for demo in self.demo_projects()['demos']:
            if demo['name'] == name:
                return Path(demo['directory'])
        raise AppError('Unknown demo project.')

    def _demo_workdir(self, name):
        """Working-copy location for a demo; the bundled originals stay pristine.

        All demo reviews, links and KiCad launches use this disposable copy.
        It lives under a dot-directory inside reports so the report scanner
        never mistakes it for a review.
        """
        return self.reports / '.demo-work' / name

    def _demo_ensure_copy(self, folder, name):
        """Create the demo's working copy on first open; reuse it afterwards.

        The copy is refreshed only by reset_demo_project, so in-progress work
        survives restarts and re-opens. Returns the working directory.
        """
        workdir = self._demo_workdir(name)
        wanted = sorted(item.name for item in folder.glob('*.kicad_pro'))
        wanted += sorted(item.name for item in folder.glob('*.kicad_pcb'))
        wanted += sorted(item.name for item in folder.glob('*.kicad_sch'))
        if (folder / 'preset-context.md').is_file():
            wanted.append('preset-context.md')
        have = sorted(item.name for item in workdir.glob('*')) if workdir.is_dir() else []
        if not all(item in have for item in wanted):
            shutil.rmtree(workdir, ignore_errors=True)
            workdir.mkdir(parents=True, exist_ok=True)
            for item in wanted:
                shutil.copy2(folder / item, workdir / item)
        return workdir

    def open_demo_project(self, name):
        """Open a bundled demo on a disposable working copy, never the originals.

        The first open snapshots the pristine demo files; later opens reuse
        the same copy so work is preserved. Reset restores the pristine
        snapshot and re-links it.

        Opening a demo switches context: any previously loaded report is
        unloaded so stale results can never linger under the new files.
        """
        folder = self._demo_directory(name)
        workdir = self._demo_ensure_copy(folder, name)
        linked = self.link_local_names(str(workdir))
        with self.lock:
            self.project, self.report_id = None, None
            self.revision += 1
        return linked

    def _demo_context_keys(self, folder, report_ids):
        """Recompute the design-context digests a demo review could have stored.

        Verified against a live review: archive is the project name and the
        identity is the main sheet's document UUID, so the name-form key is
        exact. Path-form keys are added for reviews launched with a directory
        path (e.g. CLI runs). Keys are exact digests; only equal rows are
        ever deleted.
        """
        from types import SimpleNamespace
        from .parsers.kicad_schematic import parse_schematic
        sch_files = sorted(folder.glob('*.kicad_sch'))
        linked = self.store.preference('linked_kicad_files')
        linked_sch = ''
        if isinstance(linked, dict) and linked.get('path') and \
                Path(linked['path']).resolve() == folder.resolve():
            linked_sch = linked.get('schematic') or ''
        pro = sorted(folder.glob('*.kicad_pro'))
        ordered = ([f for f in sch_files if f.name == linked_sch]
                   + [f for f in sch_files if pro and f.stem == pro[0].stem]
                   + sorted(sch_files, key=lambda f: f.name))
        uuid = None
        for candidate in dict.fromkeys(ordered):
            try:
                text = candidate.read_text(encoding='utf-8-sig', errors='replace')
                uuid = parse_schematic(text, candidate.name, instance_root=folder,
                                       project_name=folder.name).document_uuid
                if uuid:
                    break
            except (OSError, ValueError, AttributeError, KeyError):
                continue
        archives = {folder.name, str(folder), str(Path(os.path.realpath(folder)))}
        for rid in report_ids:
            pref = self.store.preference('input:' + rid)
            if isinstance(pref, dict) and pref.get('path'):
                archives.add(pref['path'])
        if linked_sch:
            archives.add(linked.get('path'))
        keys = set()
        for archive in archives:
            provenance = {'project_name': folder.name}
            selected = {}
            if uuid:
                provenance['schematic_uuid'] = uuid
            else:
                pcb = sorted(f.name for f in folder.glob('*.kicad_pcb'))
                selected = {'schematic': linked_sch or (sch_files[0].name if sch_files else ''),
                            'pcb': pcb[0] if pcb else ''}
            keys.add(context_key(SimpleNamespace(provenance=provenance,
                                                 selected_files=selected, archive=archive)))
        return keys

    def reset_demo_project(self, name):
        """Restore a bundled demo to its as-shipped, unreviewed state.

        Deletes that demo's saved reviews, fix/firmware records referencing
        them, stored answers and linked state. Board files are never touched
        (the app only ever edits disposable copies). Usage accounting is kept.
        """
        folder = self._demo_directory(name)
        workdir = self._demo_workdir(name)
        removed_reports, report_ids = [], set()
        for child in sorted(self.reports.iterdir()):
            if not child.is_dir() or child.name.startswith('.'):
                continue
            try:
                meta = json.loads((child / 'report.json').read_text(encoding='utf-8-sig'))
            except (OSError, ValueError, UnicodeError):
                continue
            if meta.get('archive') == name:
                report_ids.add(child.name)
                shutil.rmtree(child, ignore_errors=True)
                removed_reports.append(child.name)
        for record_dir in ('.file-fixes', '.fix-design', '.firmware'):
            base = self.reports / record_dir
            if not base.is_dir():
                continue
            for manifest in base.glob('*/plan.json'):
                self._drop_record_manifest(manifest, report_ids)
            for manifest in base.glob('*/record.json'):
                self._drop_record_manifest(manifest, report_ids)
        keys = self._demo_context_keys(folder, report_ids)
        if workdir.is_dir():
            keys |= self._demo_context_keys(workdir, report_ids)
        counts = self.store.reset_project(keys, report_ids)
        linked = self.store.preference('linked_kicad_files')
        if isinstance(linked, dict) and linked.get('path') and \
                Path(linked['path']).resolve() == folder.resolve():
            self.store.delete_preference('linked_kicad_files')
            counts['unlinked'] = 1
            linked = None
        if self.store.preference('last_report') in report_ids:
            self.store.delete_preference('last_report')
        if getattr(self, 'report_id', None) in report_ids:
            self.project, self.report_id = None, None
        for registry in ('file_fixes', 'fix_designs', 'firmware_jobs'):
            kept = {rid: rec for rid, rec in getattr(self, registry, {}).items()
                    if rec.get('report_id') not in report_ids}
            if len(kept) != len(getattr(self, registry, {})):
                setattr(self, registry, kept)
        # Fresh pristine working copy, re-linked so the demo is immediately
        # present again — unless the user has since moved on to a different
        # project, whose link is left alone.
        shutil.rmtree(workdir, ignore_errors=True)
        fresh = self._demo_ensure_copy(folder, name)
        relinked = None
        current = self.store.preference('linked_kicad_files')
        if not isinstance(current, dict) or not current.get('path') or \
                Path(current['path']).resolve() in (folder.resolve(), workdir.resolve()):
            relinked = self.link_local_names(str(fresh))
            counts['relinked'] = 1
        counts['removed_reports'] = len(removed_reports)
        return {'reset': name, 'removed_reports': sorted(removed_reports),
                'removed_rows': counts, 'linked': relinked['path'] if relinked else None}

    def _drop_record_manifest(self, manifest, report_ids):
        try:
            record = json.loads(manifest.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError, UnicodeError):
            return
        if record.get('report_id') in report_ids:
            shutil.rmtree(manifest.parent, ignore_errors=True)

    def open_demo_in_kicad(self, name):
        """Open a bundled demo's KiCad project in the KiCad editor.

        Opens the demo's disposable working copy, never the pristine bundled
        originals (resolved from the validated demo listing), and never a
        client-supplied path.
        """
        import subprocess
        folder = self._demo_directory(name)
        work = self._demo_ensure_copy(folder, name)
        pro = sorted(work.glob('*.kicad_pro'))
        pcb = sorted(work.glob('*.kicad_pcb'))
        target = pro[0] if pro else (pcb[0] if pcb else None)
        if target is None:
            raise AppError('This demo has no KiCad project file.')
        try:
            subprocess.Popen(['open', str(target)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, ValueError) as exc:
            raise AppError('Could not launch KiCad. Open the project manually: ' + target.name) from exc
        return {'opened': target.name, 'project': name}

    def upload_project_files(self, data):
        files = data.get('files')
        if not isinstance(files, list) or not 3 <= len(files) <= 50:
            raise AppError('Select the project, PCB, and schematic files together.')
        names=set(); total=0; validated=[]
        for item in files:
            if not isinstance(item, dict):raise AppError('Invalid project file.')
            name=item.get('name'); content=item.get('text')
            if not isinstance(name,str) or not name or len(name)>180 or '/' in name or '\\' in name or any(ord(c)<32 for c in name) or name in names:
                raise AppError('Invalid or duplicate file name.')
            if Path(name).suffix not in {'.kicad_pro','.kicad_sch','.kicad_pcb','.kicad_dru'} or not isinstance(content,str):
                raise AppError('Select KiCad project, schematic, PCB, or rules files.')
            payload=content.encode('utf-8');total+=len(payload)
            if total>30*1024*1024:raise AppError('Selected files exceed 30 MB. Use a local folder instead.')
            names.add(name);validated.append((name,payload))
        for suffix in ('.kicad_pro','.kicad_sch','.kicad_pcb'):
            if not any(name.endswith(suffix) for name in names):raise AppError('Missing '+suffix+' file.')
        with self.lock:
            self.idle_required()
            upload_id=secrets.token_hex(12);folder=Path(self.temp.name)/upload_id;folder.mkdir()
            path=folder/'selected-project.zip'
            with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                for name,payload in validated:archive.writestr(name,payload)
            previous=self.upload;self.upload={'id':upload_id,'path':path}
            if previous:
                previous['path'].unlink(missing_ok=True);previous['path'].parent.rmdir()
        return {'id':upload_id,'name':'selected-project.zip','size':total,'files':sorted(names)}

    def schematic_preview(self,data):
        run_id=data.get('report_id')
        project=restore_project(self.read_report(run_id))
        old=previous_schematic(self.reports/run_id)
        if old[0]['status']=='ready' and old[0].get('coordinate_version')==2:return old[0]
        source=(self.store.preference('input:'+run_id) or {}).get('path')
        metadata,assets=render_schematic(project,source,self.kicad_cli)
        folder=self.reports/run_id
        for name,content in assets.items():(folder/name).write_bytes(content)
        (folder/'schematic-visual.json').write_text(json.dumps(metadata))
        return metadata

    def proof(self,data):
        project=restore_project(self.read_report(data.get('report_id')))
        group=next((g for g in project.review.get('groups',[]) if g['id']==data.get('group_id')),None)
        if not group:raise AppError('Choose an issue from this report.',404)
        return issue_proof(project,group,self.reports)

    @guarded_chat
    def project_chat(self, data):
        question=data.get('question','');history=data.get('history',[])
        web_search=data.get('web_search',False)
        if type(web_search) is not bool:raise AppError('Choose whether to search the web.')
        if not isinstance(question,str) or not question.strip() or len(question)>4000 or '\x00' in question:
            raise AppError('Write a message under 4,000 characters.')
        if not isinstance(history,list) or len(history)>6 or any(not isinstance(h,dict)
                or h.get('role') not in ('user','assistant') or not isinstance(h.get('text'),str)
                or len(h['text'])>4000 or '\x00' in h['text'] for h in history):
            raise AppError('Invalid conversation history. Start a new chat.')
        with self.lock:
            report_id=data.get('report_id')
            if report_id is not None and (not self.project or report_id!=self.report_id):
                raise AppError('The project changed. Send your message again from the current chat.',409)
            project=deepcopy(self.project) if report_id is not None else None
            if project:
                review_revision=project.design_context.get('revision',0)
                project_key=context_key(project)
                if not self.store.design_context(project_key):
                    self.store.save_context(project_key,project.design_context or empty_context(),0)
                project.design_context=self.store.design_context(project_key)
                project.manufacturing=self.manufacturing_result(project,self.report_id)
                from .chat_decisions import prepare as prepare_decisions
                prepare_decisions(self.store,project,'general',report_id,question.strip())
                request_id=secrets.token_hex(16)
                prompt=next((h['text'] for h in reversed(history) if h['role']=='assistant'),'')
                self.store.save_conversation(project_key,report_id,'general',request_id,question.strip(),prompt)
                project.design_context=self.store.design_context(project_key)
                source=(self.store.preference('input:'+report_id) or {}).get('path')
                project.provenance['chat_evidence']=evidence_state(project,source,review_revision)
                self.revision+=1
            key,model=self.keys_for_ai(),self.model
        with request_budget(self.usage_mode(), 'chat') as budget, meta_action('chat'):
            result=answer_project(project,question.strip(),history,key=key,model=model,web_search=web_search)
            if project:
                from .chat_decisions import finish as finish_decisions
                result=finish_decisions(self,project,'general',report_id,question.strip(),result,key,model,source)
                latest_revision=(self.store.design_context(project_key) or {}).get('revision',0)
                result={**result,'evidence_state':evidence_state(project,source,review_revision,latest_revision)}
                self.store.finish_conversation(project_key,'general',request_id,result)
                with self.lock:self.revision+=1
            from .explanation_profile import explanation_profile
            return {**result,'request_usage':budget.summary(),
                    'explanation_profile':explanation_profile(project.design_context) if project else None}

    @guarded_chat
    def step_help(self, data):
        question = data.get('question', '')
        group_id = data.get('group_id', '')
        step_index = data.get('step_index', 0)
        history = data.get('history', [])
        if not isinstance(question, str) or not question.strip() or len(question) > 1000 or '\x00' in question:
            raise AppError('Write a short question about this step.')
        if not isinstance(group_id, str) or type(step_index) is not int or not isinstance(history, list):
            raise AppError('Choose a step from the current plan.')
        if len(history) > 6 or any(not isinstance(item, dict) or item.get('role') not in ('user', 'assistant')
                                 or not isinstance(item.get('text'), str) or len(item['text']) > 4000
                                 for item in history):
            raise AppError('Chat history is too long. Start a new question.')
        with self.lock:
            if not self.project or data.get('report_id') != self.report_id:
                raise AppError('Open the current report before asking about a step.', 409)
            self.project.manufacturing = self.manufacturing_result(self.project, self.report_id)
            group = next((g for g in self.project.review.get('groups', []) if g['id'] == group_id), None)
            from .chat_decisions import apply_review as apply_chat_review
            live_context=self.store.design_context(context_key(self.project)) or self.project.design_context
            chat_plan=apply_chat_review(current_plan(self.project),self.store.preference('chat_review:'+self.report_id),self.project,live_context)
            action = next((a for a in chat_plan.get('actions', []) if a['group_id'] == group_id), None)
            if action and not action.get('steps'):
                action={**action, 'steps':['Explain what this finding means and why no edit is indicated.']}
            if not group or not action or not 0 <= step_index < len(action.get('steps', [])):
                raise AppError('Choose a step from the current plan.')
            project = deepcopy(self.project)
            review_revision=project.design_context.get('revision',0)
            project_key=context_key(project)
            if not self.store.design_context(project_key):
                self.store.save_context(project_key,project.design_context or empty_context(),0)
            project.design_context=self.store.design_context(project_key)
            request_id=secrets.token_hex(16)
            stable_group=next(g for g in keyed_groups(asdict(project)) if g['id']==group_id)
            channel='issue-key:'+stable_group['key']
            rows=self.store.conversations(project_key,channel,limit=20)
            legacy=[r for r in self.store.conversations(project_key,'issue:'+group_id,limit=20)
                    if r['report_id']==self.report_id]
            # Only this issue's conversation is working memory. Explicit product
            # answers remain shared; unrelated chat is not an issue's transcript.
            project.design_context['conversation_memory']=[{
                k:r[k] for k in ('channel','question','prompt','report_id','at')}
                for r in sorted(rows+legacy,key=lambda r:r['id'])[-20:]]
            prompt=next((h['text'] for h in reversed(history) if h['role']=='assistant'),group.get('title',''))
            from .chat_decisions import prepare as prepare_decisions
            prepare_decisions(self.store,project,channel,self.report_id,question.strip())
            self.store.save_conversation(project_key,self.report_id,channel,request_id,question.strip(),prompt)
            project.design_context['explanation_preferences']=self.store.preference('explanation:'+project_key) or {}
            source=(self.store.preference('input:'+self.report_id) or {}).get('path')
            project.provenance['chat_evidence']=evidence_state(project,source,review_revision)
            self.revision+=1
            key, model = self.keys_for_ai(), self.model
        with request_budget(self.usage_mode(), 'chat') as budget, meta_action('chat'):
            result = answer_step(project, group, action, step_index, question.strip(), history, key=key, model=model)
            from .chat_decisions import finish as finish_decisions
            result=finish_decisions(self,project,channel,data['report_id'],question.strip(),result,key,model,source)
            latest_revision=(self.store.design_context(project_key) or {}).get('revision',0)
            result={**result,'evidence_state':evidence_state(project,source,review_revision,latest_revision)}
            self.store.finish_conversation(project_key,channel,request_id,result)
            with self.lock:self.revision+=1
            from .explanation_profile import explanation_profile
            return {**result, 'request_usage': budget.summary(),
                    'explanation_profile':explanation_profile(project.design_context)}

    def manufacturing_result(self, project, report_id=None, source=None, measure=False):
        selection = self.store.preference('manufacturer:' + context_key(project))
        if selection is None:
            selection = project.manufacturing.get('selection', {})
        measured = project.signal_paths.get('board_measurements', {}).get('fabrication')
        if report_id:
            cached = self.store.preference('fabrication:' + report_id)
            if cached and (not measured or cached.get('version',0) > measured.get('version',0)):
                measured = cached
        if measure and (not measured or measured.get('version',0) < 2):
            if not source and report_id:
                source = (self.store.preference('input:' + report_id) or {}).get('path')
            measured = measure_source(project, source)
            if report_id and measured.get('status') == 'measured':
                self.store.preference('fabrication:' + report_id, measured)
        return evaluate_manufacturing(asdict(project), selection, measured)

    def select_manufacturer(self, data):
        with self.lock:
            self.idle_required()
            if not self.project or data.get('report_id') != self.report_id:
                raise AppError('Open the current project before checking a manufacturer.', 409)
            try:
                selection = validate_selection(data.get('selection'))
            except ValueError as exc:
                raise AppError(str(exc)) from None
            self.store.preference('manufacturer:' + context_key(self.project), selection)
            self.project.manufacturing = self.manufacturing_result(self.project, self.report_id, measure=True)
            self.revision += 1
            return self.project.manufacturing

    def snapshot(self):
        with self.lock:
            return {'job': deepcopy(self.job), 'revision': self.revision, 'report_id': self.report_id}

    def save_verification(self, request):
        from .verification import edit
        with self.lock:
            self.idle_required()
            if not self.project or request.get('report_id') != self.report_id:
                raise AppError('Open the current project before saving calculation limits.', 409)
            key = 'verification:' + context_key(self.project)
            current = self.store.design_context(key) or self.project.verification
            try:
                design = asdict(self.project)
                design['design_context'] = self.store.design_context(context_key(self.project)) or self.project.design_context
                value = edit(design, request, current)
                saved = self.store.save_context(key, value, request.get('revision'))
            except RevisionConflict as exc:
                raise AppError('These limits changed in another tab. Refresh before saving.', 409) from exc
            except ValueError as exc:
                raise AppError(str(exc)) from None
            self.project.verification = saved
            self.revision += 1
            return {'revision':saved['revision']}

    def measure_engineering_geometry(self, request):
        """Upgrade a saved analyzed snapshot locally, without an AI request or CAD edits."""
        from .routing_geometry import measure
        from .parsers.sexpr import parse
        with self.lock:
            self.idle_required()
            if not self.project or request.get('report_id') != self.report_id:
                raise AppError('Open the current review before measuring routing.',409)
            project=self.project; report_id=self.report_id
            expected=project.board_checks.get('analyzed_pcb_sha256')
            if project.board_checks.get('status')!='prepared' or not expected:
                raise AppError('Run Update review first to produce a refilled PCB snapshot.')
            path=self.reports/report_id/'analyzed-board.kicad_pcb'
        try:
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=expected:
                raise AppError('The analyzed PCB changed. Update the review before measuring routing.',409)
            result=measure(parse(raw.decode('utf-8'),'kicad_pcb'))
        except AppError:
            raise
        except (OSError,ValueError) as exc:
            raise AppError('Could not measure the saved PCB: '+str(exc)) from None
        with self.lock:
            if self.project is not project or self.report_id!=report_id or self.busy():
                raise AppError('The review changed during measurement. Open the current review and try again.',409)
            self.project.board_checks['signal_geometry']=result
            self.revision+=1
        return {'status':result['status']}

    def save_check_scope(self, request):
        from .check_scope import project_scope, YES, NO, UNKNOWN
        with self.lock:
            self.idle_required()
            if not self.project or request.get('report_id') != self.report_id:
                raise AppError('Open the current project before changing its check plan.',409)
            key=context_key(self.project)
            current=self.store.design_context(key) or self.project.design_context or empty_context()
            scope=project_scope(self.project,current)
            if request.get('signature')!=scope['signature']:
                raise AppError('The project context changed. Refresh its check plan before saving.',409)
            values=request.get('answers')
            ids={q['id'] for q in scope['questions']}
            if not isinstance(values,dict) or set(values)!=ids or any(not isinstance(v,str) or v not in ('',YES,NO,UNKNOWN) for v in values.values()):
                raise AppError('Choose one of the displayed product-use answers for each question.')
            try:
                saved=self.store.save_context(key,edit_context(self.project,current,{'product_context':values}),request.get('context_revision'))
            except RevisionConflict as exc:
                raise AppError('Product context changed in another tab. Refresh before saving.',409) from exc
            except ValueError as exc:
                raise AppError(str(exc)) from None
            self.revision+=1
            return {'revision':saved['revision']}

    def project_context(self, request):
        """Free-text project context, editable and saved with the project.

        Flows into analysis evidence (design_context.description) and firmware
        discovery purpose. It informs interpretation but never overrides
        measured electrical evidence or datasheet requirements.
        """
        with self.lock:
            if not self.project or request.get('report_id') != self.report_id:
                raise AppError('Open the current project before editing its context.', 409)
            key = context_key(self.project)
            current = self.store.design_context(key) or self.project.design_context or empty_context()
            if 'description' in request:
                text = request.get('description')
                if not isinstance(text, str):
                    raise AppError('Describe the project in text.')
                try:
                    saved = self.store.save_context(
                        key, edit_context(self.project, current, {'description': text}),
                        request.get('context_revision'))
                except RevisionConflict as exc:
                    raise AppError('Project context changed in another tab. Refresh before saving.', 409) from exc
                except ValueError as exc:
                    raise AppError(str(exc)) from None
                self.project.design_context = saved
                self.revision += 1
                return {'description': saved.get('description', ''),
                        'context_revision': saved.get('revision', 0)}
            return {'description': (current or {}).get('description', ''),
                    'context_revision': (current or {}).get('revision', 0)}

    def product_answers(self, request, save=False):
        """Save a submitted answer immediately, independently of any AI run."""
        from .product_context import merge_answers
        with self.lock:
            if not self.project or request.get('report_id')!=self.report_id:
                raise AppError('Open the current project before saving an answer.',409)
            key=context_key(self.project)
            current=self.store.design_context(key) or self.project.design_context or empty_context()
            try:
                if save:
                    updated=edit_context(self.project,current,{'product_context':request.get('answers',{})})
                    current=self.store.save_context(key,updated,request.get('context_revision'))
                    self.revision+=1
                else:
                    # Upgrade saved answer IDs and retain their question text
                    # once, while this revision still identifies their basis.
                    updated=edit_context(self.project,current,{'product_context':{}})
                    if any(updated.get(k)!=current.get(k) for k in ('product_context','product_questions')):
                        current=self.store.save_context(key,updated,current.get('revision',0))
                        self.revision+=1
                answers=merge_answers(current.get('product_context',{}),{} if save else request.get('answers',{}))
                from .user_profile import guided_questions, check_flow_questions
                result=guided_questions(self.project,answers,context=current)
                if request.get('flow')=='check':
                    result=check_flow_questions(result)
            except RevisionConflict as exc:raise AppError(str(exc),409) from exc
            except ValueError as exc:raise AppError(str(exc)) from exc
            return {**result,'report_id':self.report_id,'context_revision':current.get('revision',0)}

    def save_question_answer(self, request):
        from .workflow import digest
        with self.lock:
            if not self.project or request.get('report_id')!=self.report_id:
                raise AppError('Open the current project before saving an answer.',409)
            question=request.get('question');answer=request.get('answer')
            if not isinstance(question,str) or not question.strip() or len(question)>4000 or not isinstance(answer,str) or not answer.strip() or len(answer)>4000 or '\x00' in question+answer:
                raise AppError('Enter a question and answer under 4,000 characters each.')
            key=context_key(self.project)
            current=self.store.design_context(key) or self.project.design_context or empty_context()
            saved_answers=dict(current.get('question_answers',{}))
            identity=digest(question.casefold().strip())
            task_record = None
            if request.get('review_task') is not None:
                from .review_inputs import review_inputs
                from .verification import evaluate
                from .release_review import before_ordering
                design=asdict(self.project)
                design['design_context']=current
                design['verification']=self.store.design_context('verification:'+key) or self.project.verification
                design['verification_results']=evaluate(design)
                design['before_ordering']=before_ordering(design)
                task=next((t for t in review_inputs(design)['tasks']
                           if {'id':t['id'],'signature':t['signature']}==request['review_task'] and t['question']==question.strip()),None)
                if not task:raise AppError('This review question changed. Reopen the current review.',409)
                task_record={'id':task['id'],'signature':task['signature']}
                identity=digest('review-task:'+task['id']+':'+task['signature'])
            if identity not in saved_answers:
                # The interview dialog saves task-bound while the questions
                # form saves plain; without this the same question
                # accumulates duplicate slots that disagree with each other.
                norm=' '.join(question.strip().split()).casefold()
                for ident, rec in saved_answers.items():
                    if isinstance(rec, dict) and ' '.join(str(rec.get('question','')).split()).casefold()==norm:
                        identity=ident
                        break
            prior=saved_answers.get(identity) if isinstance(saved_answers.get(identity),dict) else {}
            saved_answers[identity]={'question':question.strip(),'answer':answer.strip(),'source':'User answer',
                'report_id':self.report_id,'updated':now()}
            if task_record:saved_answers[identity]['review_task']=task_record
            else:
                # Keep the slot's existing task binding and routing when this
                # save does not supply new ones; the write above replaces the
                # whole record.
                for keep in ('review_task','question_creator'):
                    if prior.get(keep):saved_answers[identity][keep]=prior[keep]
            routed = None
            qc = request.get('question_creator')
            if isinstance(qc, dict) and qc.get('decision_id') and qc.get('originating_agent'):
                saved_answers[identity]['question_creator'] = {
                    'decision_id': str(qc['decision_id'])[:64],
                    'originating_agent': str(qc['originating_agent'])[:64]}
                routed = {'decision_id': saved_answers[identity]['question_creator']['decision_id'],
                          'originating_agent': saved_answers[identity]['question_creator']['originating_agent'],
                          'resume': 'resume %s with this fact; re-run verification before closing the finding' % (
                              saved_answers[identity]['question_creator']['originating_agent'])}
            try:
                saved=self.store.save_context(key,{**current,'question_answers':saved_answers},request.get('context_revision'))
            except RevisionConflict as exc:
                if (self.store.design_context(key) is None
                        and request.get('context_revision') == current.get('revision')):
                    # A re-linked project can carry a disk revision with no
                    # store row yet; the posted revision then matches nothing
                    # and every save is rejected. Seed this key once from the
                    # opened context and retry a single time.
                    self.store.save_context(key, current, 0)
                    saved=self.store.save_context(key,{**current,'question_answers':saved_answers},1)
                else:raise AppError(str(exc),409) from exc
            self.revision+=1
            receipt = {'revision':saved['revision'],'saved':saved_answers[identity]}
            if routed:receipt['routed'] = routed
            return receipt

    def investigation_data(self):
        """Live VECTOR pipeline for the Command Center. Never includes credentials."""
        with self.lock:
            review = (self.project.ai_review if self.project else None) or {}
            pipeline = review.get('pipeline')
            if not pipeline:
                live = getattr(self, 'live_pipeline', None)
                job = getattr(self, 'job', None)
                if live and job and job.get('status') == 'running':
                    return {'status': 'running', 'model': review.get('model', ''),
                            'pipeline': live,
                            'result': {'actions': [], 'questions': [], 'summary': '', 'limitations': []},
                            'usage': {}, 'request_usage': {}}
                return {'status': 'empty'}
            result = review.get('result', {})
            return {'status': review.get('status', ''), 'model': review.get('model', ''),
                    'pipeline': pipeline,
                    'result': {k: result.get(k) for k in ('actions', 'questions', 'summary', 'limitations')},
                    'usage': review.get('usage', {}), 'request_usage': review.get('request_usage', {})}

    def project_data(self):
        with self.lock:
            if not self.project:
                return None
            # Native JSON is available as a download; don't resend it to render the UI.
            self.project.manufacturing = self.manufacturing_result(self.project, self.report_id)
            data = asdict(self.project)
            if data.get('ai_review',{}).get('circuit_audit'):
                from .ai.circuit_audit import bound_saved_audit
                bound_saved_audit(data['ai_review']['circuit_audit'])
            data['verification'] = self.store.design_context('verification:' + context_key(self.project)) or self.project.verification
            context = self.store.design_context(context_key(self.project)) or self.project.design_context or empty_context()
            data['design_context'] = context
            from .explanation_profile import explanation_profile
            data['explanation_profile'] = explanation_profile(context)
            from .verification import evaluate
            data['verification_results'] = evaluate(data)
            from .release_review import before_ordering
            if 'routing_chains' not in data.get('circuit', {}):
                from .routing_chains import build_routing_chains
                data.setdefault('circuit', {})['routing_chains'] = build_routing_chains(self.project)
            data.pop('native_reports', None)
            data['action_plan'] = current_plan(self.project, data)
            from .component_phrases import describe_all
            data['component_descriptions'] = describe_all(data)
            data['review']['groups'] = keyed_groups(data)
            data['file_fix_options'] = {g['id']: file_fixes.availability(self.project, g)
                                       for g in data['review']['groups']}
            stale = context['revision'] != self.project.design_context.get('revision', 0)
            if stale or not data.get('action_plan'):
                view = deepcopy(self.project)
                view.design_context = context
                data['action_plan'] = local_plan(view)
            if stale:
                data['ai_review'] = {'status': 'stale', 'message': 'The design context changed after this Vector review. Update recommendations to use the latest context.'}
            from .chat_decisions import apply_review as apply_chat_review
            data['action_plan']=apply_chat_review(data['action_plan'],self.store.preference('chat_review:'+self.report_id),self.project,context)
            data['saved_review_questions']=self.project.ai_review.get('result',{}).get('questions',[])
            from .check_scope import build_scope
            data['check_scope'] = build_scope({**data,'design_context':context})
            data['before_ordering'] = before_ordering(data)
            from .review_inputs import review_inputs
            data['review_inputs'] = review_inputs(data)
            from .user_profile import guided_questions
            data['review_inputs']['needs_product_profile']=bool(guided_questions(self.project,context=context)['unanswered'])
            from .review_sections import organize_review
            data['review_sections'] = organize_review(data)
            data['datasheets']['excerpts'] = excerpts(self.project)
            for document in data['datasheets'].get('documents', {}).values():
                document.pop('pages', None)
            decisions = self.store.decisions(self.report_id) if self.report_id else {}
            input_source = self.store.preference('input:'+self.report_id) if self.report_id else None
            source_path = (input_source or {}).get('path')
            source_check = file_fixes.source_status(
                source_path, data.get('provenance', {}).get('source_hashes', {}))
            if source_check['status'] == 'changed':
                data['ai_review'] = {'status': 'stale', 'message':
                    'The linked KiCad files changed after this review. Update the review before using its advice.'}
            data['workspace'] = {'report_id':self.report_id,'decisions':decisions,
                                 'project_key':context_key(self.project),
                                 'design_context': context, 'context_stale': stale,
                                 'source_status': source_check,
                                 'readiness':readiness(data,decisions),
                                 'input_source':input_source,
                                 'events':self.store.events(self.report_id) if self.report_id else []}
            data['workspace']['chat_context_handled']=self.store.preference('chat_handled:'+context_key(self.project))=={'report_id':self.report_id,'context_revision':context['revision']}
            data['workspace']['chat_history']=self.store.conversations(context_key(self.project),'general',limit=100)
            data['workspace']['conversation_history']=self.store.conversations(context_key(self.project),limit=100)
            data['workspace']['file_fixes'] = [{k:r.get(k) for k in ('id','group_id','status','summary')}
                for r in self.file_fixes.values() if r.get('report_id') == self.report_id and r.get('status') in ('ready','applied','awaiting_design_approval','design_approved')]
            if self.report_id:
                try:
                    from .board_locations import enrich_visual, merge_verified_source_artifacts
                    visual_path = self.report_path(self.report_id, 'board-visual.json')
                    visual = json.loads(visual_path.read_text(encoding='utf-8'))
                    enriched = enrich_visual(visual, self.reports/self.report_id/'analyzed-board.kicad_pcb',
                                             self.project.board_checks.get('analyzed_pcb_sha256'))
                    if enriched != visual:
                        visual_path.write_text(json.dumps(enriched), encoding='utf-8')
                    response_visual=enriched
                    # Some KiCad copy/refill paths omit netless board artwork.
                    # Merge only off-outline copper from the unchanged original
                    # PCB, verified against the hash captured during analysis.
                    input_path=(data['workspace'].get('input_source') or {}).get('path')
                    pcb_relative=data.get('selected_files',{}).get('pcb')
                    source_hash=data.get('provenance',{}).get('source_hashes',{}).get(pcb_relative)
                    if input_path and pcb_relative and source_hash:
                        try:
                            source_content=read_project_file(input_path,pcb_relative)
                            response_visual=merge_verified_source_artifacts(response_visual,source_content,source_hash)
                        except (ArchiveError, OSError):
                            pass
                    data['workspace']['board_visual'] = response_visual
                except (AppError, ArchiveError, OSError, ValueError):
                    data['workspace']['board_visual'] = {'status':'unavailable','message':'Run a new check to render the board.'}
            if self.report_id:
                data['workspace']['schematic_visual']=previous_schematic(self.reports/self.report_id)[0]
            return data

    def report_path(self, report_id, filename='report.json'):
        if not isinstance(report_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,180}', report_id) or filename not in REPORT_FILES:
            raise AppError('Report not found.', 404)
        folder = self.reports / report_id
        path = folder / filename
        if folder.is_symlink() or path.is_symlink() or folder.resolve().parent != self.reports or not path.is_file():
            raise AppError('Report not found.', 404)
        return path

    def read_report(self, report_id):
        path = self.report_path(report_id)
        if path.stat().st_size > 30 * 1024 * 1024:
            raise AppError('This saved report is too large to open in the app.')
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('archive'), str):
                raise ValueError()
            return data
        except (ValueError, OSError):
            raise AppError('This saved report could not be read.') from None

    def sync_history(self):
        """Import legacy reports once; later refreshes read only new/changed files."""
        known = self.store.known()
        for folder in self.reports.iterdir():
            if not folder.is_dir() or folder.is_symlink():
                continue
            try:
                stat = self.report_path(folder.name).stat()
                if known.get(folder.name) == stat.st_mtime_ns:
                    continue
                data = self.read_report(folder.name)
                self.store.index(folder.name,data,mtime_ns=stat.st_mtime_ns,
                                 created=datetime.fromtimestamp(stat.st_mtime,timezone.utc).isoformat())
            except (AppError, OSError, TypeError, AttributeError):
                continue

    def history(self):
        return self.store.library(limit=100)['items']

    def annotate(self,data):
        with self.lock:
            self.idle_required()
            run_id=data.get('report_id')
            report=self.read_report(run_id)
            group=next((g for g in keyed_groups(report) if g['key']==data.get('key')),None)
            if not group:
                raise AppError('This finding does not belong to the selected run.',404)
            try:
                result=self.store.annotate(run_id,group['key'],group['signature'],data.get('status'),
                                           data.get('note',''),data.get('revision',0))
            except RevisionConflict as exc:
                raise AppError(str(exc),409) from None
            except ValueError as exc:
                raise AppError(str(exc)) from None
            self.revision+=1
            return result

    def compare(self,data):
        before_id,after_id=data.get('before'),data.get('after')
        before,after=self.read_report(before_id),self.read_report(after_id)
        a,b=self.store.run(before_id),self.store.run(after_id)
        if not a or not b or a['project'].casefold()!=b['project'].casefold():
            raise AppError('Select two runs from the same named project.')
        if a['source_uuid'] and b['source_uuid'] and a['source_uuid']!=b['source_uuid']:
            raise AppError('These runs have different root schematic identities. Choose matching revisions.')
        if before_id==after_id:
            raise AppError('Choose a different baseline run.')
        return compare_runs(before,after) | {'before':before_id,'after':after_id}

    def bundle(self,run_id):
        report=self.read_report(run_id)
        board_files={}
        for name in ('board-front.svg','board-back.svg','board-visual.json','schematic.svg','schematic-visual.json'):
            try:
                board_files[name]=self.report_path(run_id,name).read_bytes()
            except AppError:
                pass
        return review_bundle(report,self.store.decisions(run_id),self.store.events(run_id),
                             self.report_path(run_id,'report.html').read_bytes(),board_files)

    def _luna_settings(self, data):
        if self.keychain_loading:
            raise AppError('Saved AI keys are still loading. Wait a moment, then save again.',409)
        primary=data.get('openai_key', data.get('key',''))
        backup=data.get('gemini_key','')
        for value in (primary,backup):
            if not isinstance(value,str) or len(value)>512 or any(ord(c)<33 or ord(c)>126 for c in value.strip()):
                raise AppError('Enter a complete API key without whitespace.')
        primary=primary.strip();backup=backup.strip()
        if primary and not primary.startswith('sk-'):
            raise AppError('Use an OpenAI API key beginning with sk- for Luna. Your Gemini key belongs in the backup field.')
        if backup.startswith(('gen-lang-client-','sk-')):
            raise AppError('Use a Gemini API key in the backup field.')
        remember=data.get('remember_key',self.openai_key_saved)
        enabled=data.get('gemini_backup_enabled',self.gemini_backup_enabled)
        if type(remember) is not bool or type(enabled) is not bool:raise AppError('Invalid Vector settings.')
        candidate=primary or self.openai_key
        try:
            if data.get('clear_key') is True:
                for account in (account_for(self.reports),account_for(self.reports)+'.openai',account_for(self.reports)+'.meta'):
                    Keychain(account).delete()
                Keychain(meta_account(), service=META_SERVICE).delete()
                self.openai_key='';self.openai_key_saved=False
                self.key='';self.fallback_keys=[];self.key_saved=False
                self.store.preference('remember_openai_key',False)
                self.store.preference('remember_gemini_key',False)
                self.meta_key='';self.meta_key_saved=False
                self.store.preference('remember_meta_key',False)
            else:
                if remember and candidate:
                    Keychain(account_for(self.reports)+'.openai').save(candidate)
                    self.openai_key_saved=True
                elif self.openai_key_saved:
                    Keychain(account_for(self.reports)+'.openai').delete()
                    self.openai_key_saved=False
                self.openai_key=candidate
                self.store.preference('remember_openai_key',bool(remember and candidate))
                # The existing primary Gemini key stays in its original entry.
                # Additional old Gemini keys remain stored but are not used by Luna.
                if backup:
                    saved=[backup,*self.fallback_keys]
                    if remember:
                        Keychain(account_for(self.reports)).save(json.dumps(saved,separators=(',',':')))
                    elif self.key_saved:
                        Keychain(account_for(self.reports)).delete()
                    self.key=backup;self.key_saved=remember
                    self.store.preference('remember_gemini_key',remember)
        except KeychainError as exc:raise AppError(str(exc)) from None
        self.model='gpt-6-luna'
        self.gemini_backup_enabled=enabled
        self.store.preference('gemini_backup_enabled',enabled)
        self.store.preference('ai_model',self.model)
        self.key_storage_error='';self._credential_revision+=1
        return self.config()

    def usage_mode(self):
        """Single complete-investigation tier; legacy modes migrate to it."""
        mode = self.store.preference('ai_usage_mode')
        return mode if mode in LIMITS else 'complete'

    def settings(self, data):
        with self.lock:
            mode = data.get('ai_usage_mode', self.store.preference('ai_usage_mode'))
            if mode is None and 'ai_usage_mode' not in data:
                mode = 'complete'
            if mode in ('economy', 'detailed'):
                mode = 'complete'
            if not isinstance(mode, str) or mode not in LIMITS:
                raise AppError('Choose complete Vector investigation.')
            if any(k in data for k in ('model', 'meta_key', 'openai_key', 'key', 'keys',
                                       'clear_key', 'clear_key_slots', 'remember_key')):
                self._settings(data)
            # A mode-only update must never silently switch providers: the
            # legacy key path defaults to Gemini when no model is specified.
            self.store.preference('ai_usage_mode', mode)
            return self.config()

    def _meta_settings(self, data):
        if self.keychain_loading:
            raise AppError('Saved keys are still loading. Wait a moment, then save again.',409)
        raw_model=data.get('model','')
        if not isinstance(raw_model,str):raise AppError('Enter the Meta model ID.')
        try:model=meta_model_id(raw_model)
        except ValueError as exc:raise AppError(str(exc)) from None
        key=data.get('meta_key',data.get('key',''))
        if not isinstance(key,str) or len(key)>2048 or any(ord(c)<33 or ord(c)>126 for c in key.strip()):
            raise AppError('Enter a complete Meta API key without whitespace.')
        candidate='' if data.get('clear_key') is True else key.strip() or self.meta_key
        remember=data.get('remember_key',self.meta_key_saved)
        if type(remember) is not bool:raise AppError('Choose whether to remember this key.')
        try:
            if remember and candidate:Keychain(meta_account(), service=META_SERVICE).save(candidate)
            elif self.meta_key_saved:Keychain(meta_account(), service=META_SERVICE).delete()
        except KeychainError as exc:raise AppError(str(exc)) from None
        self.meta_key=candidate;self.meta_key_saved=bool(remember and candidate)
        self.store.preference('remember_meta_key',self.meta_key_saved)
        self.store.preference('meta_model',model)
        self.model='meta/'+model;self.store.preference('ai_model',self.model)
        self.key_storage_error='';self._credential_revision+=1
        return self.config()

    def _settings(self, data):
        with self.lock:
            self.idle_required()
            model = data.get('model', self.model)
            if 'model' not in data and 'meta_key' not in data and 'openai_key' not in data:
                # The legacy key-only call shape predates multi-provider settings
                # and can only address the Gemini key slots; the UI always sends
                # an explicit model and is unaffected.
                model = PROVIDERS['gemini']['model']
            if is_meta(model):return self._meta_settings(data)
            if model=='gpt-6-luna':return self._luna_settings(data)
            key = data.get('key', '')
            if not isinstance(model, str) or not re.fullmatch(r'gemini-[A-Za-z0-9_.-]{1,100}', model):
                raise AppError('Enter a Gemini model name, such as gemini-3.5-flash-lite.')
            if not isinstance(key, str) or len(key) > 512:
                raise AppError('Invalid API key.')
            key = key.strip()
            if key.startswith('gen-lang-client-'):
                raise AppError('That is a Google project ID. Copy the API key from AI Studio.')
            if key and any(ord(c) < 33 or ord(c) > 126 for c in key):
                raise AppError('The API key contains whitespace or invalid characters.')
            current=self.gemini_keys()+['']*3
            slots=data.get('keys')
            if slots is not None:
                if not isinstance(slots,list) or len(slots)!=3 or any(not isinstance(item,str) or len(item)>512 for item in slots):
                    raise AppError('Enter up to three Gemini API keys.')
                for index,value in enumerate(slots):
                    value=value.strip()
                    if value:
                        current[index]=value
            elif key:
                current[0]=key
            clear_slots=data.get('clear_key_slots',[])
            if not isinstance(clear_slots,list) or any(type(index) is not int or index not in (1,2) for index in clear_slots):
                raise AppError('Invalid fallback-key selection.')
            for index in clear_slots:current[index]=''
            candidate=[value for value in current if value]
            # Retain slot order even if the primary key is removed.
            if slots is not None:
                candidate=current[:3]
            if any(value.startswith('gen-lang-client-') for value in candidate):
                raise AppError('That is a Google project ID. Copy the API key from AI Studio.')
            if any(any(ord(c) < 33 or ord(c) > 126 for c in value) for value in candidate):
                raise AppError('An API key contains whitespace or invalid characters.')
            remember=data.get('remember_key',self.key_saved)
            if type(remember) is not bool:raise AppError('Choose whether to remember the key.')
            if self.keychain_loading and not key and slots is None and data.get('clear_key') is not True:
                raise AppError('Your saved Gemini key is still loading. Wait a moment, then save settings again.', 409)
            try:
                if data.get('clear_key') is True:
                    if self.key_saved or self.store.preference('remember_gemini_key'):
                        Keychain(account_for(self.reports)).delete()
                    self.key='';self.fallback_keys=[];self.key_saved=False
                    self.store.preference('remember_gemini_key',False)
                else:
                    if remember:
                        if not candidate:raise AppError('Add at least one Gemini API key first.')
                        Keychain(account_for(self.reports)).save(json.dumps(candidate,separators=(',',':')))
                        self.key_saved=True
                    elif self.key_saved:
                        Keychain(account_for(self.reports)).delete()
                        self.key_saved=False
                    self.key=candidate[0] if candidate else ''
                    self.fallback_keys=candidate[1:]
                    self.store.preference('remember_gemini_key',remember)
            except KeychainError as exc:
                raise AppError(str(exc)) from None
            self.key_storage_error=''
            self._credential_revision += 1
            self.keychain_loading = False
            self.model=model
            self.gemini_model=model
            self.store.preference('gemini_model',model)
            self.store.preference('ai_model',model)
            return self.config()

    def open_report(self, report_id):
        with self.lock:
            self.idle_required()
            try:
                project = restore_project(self.read_report(report_id))
            except (TypeError, KeyError, AttributeError, ValueError) as exc:
                if isinstance(exc, AppError):
                    raise
                raise AppError('This report uses an unsupported format.') from None
            self.project, self.report_id = project, report_id
            self.store.preference('last_report',report_id)
            self.job = None
            self.revision += 1
            return self.snapshot()

    def start(self, kind, data):
        with self.lock:
            self.idle_required()
            if kind not in ('analysis', 'question', 'connection', 'rethink', 'review'):
                raise AppError('Unknown check.')
            use_ai = data.get('ai') is True or kind in ('question', 'connection', 'review')
            if use_ai and self.keychain_loading:
                raise AppError('Checking for your saved AI key in macOS Keychain. Wait a moment, then try again.', 409)
            if use_ai and self.store.preference('ai_model') is None:
                # Keys can arrive after startup (session key, Keychain load):
                # resolve the default when AI is actually used, never stranding
                # a configured key on an unkeyed model.
                self.model = self._default_model()
            if use_ai and not self.keys_for_ai():
                raise AppError('Add your primary API key in Vector settings, or turn off Vector review.')
            question = data.get('question', '')
            if not isinstance(question, str) or len(question) > 4000:
                raise AppError('Keep your question under 4,000 characters.')
            if kind == 'question' and (not self.project or not question.strip()):
                raise AppError('Open a completed run and enter a question first.')
            if kind == 'review':
                if not self.project or data.get('report_id') != self.report_id:
                    raise AppError('Open the current review before retrying AI.', 409)
                source = (self.store.preference('input:'+self.report_id) or {}).get('path')
                if source and Path(source).is_dir():
                    status = file_fixes.source_status(source, self.project.provenance.get('source_hashes', {}))
                    if status['status'] == 'changed':
                        raise AppError('The linked KiCad files changed or cannot be read. Use Update review to check the saved changes before retrying AI.', 409)
            context = None
            if kind == 'analysis' and 'product_context' in data:
                try:
                    data = {**data, 'product_context': validate_answers(data['product_context'])}
                except ValueError as exc:
                    raise AppError(str(exc)) from None
            if kind == 'rethink':
                if not self.project or not self.report_id or data.get('report_id') != self.report_id:
                    raise AppError('Open the current completed run before updating its context.', 409)
                try:
                    key_id = context_key(self.project)
                    current = self.store.design_context(key_id) or empty_context()
                    context = self.store.save_context(key_id, edit_context(self.project, current, data), data.get('context_revision'))
                except RevisionConflict as exc:
                    raise AppError(str(exc), 409) from None
                except ValueError as exc:
                    raise AppError(str(exc)) from None
                if 'part_choice' in data:
                    data = {**data, 'datasheets': True}
            source = None
            if kind == 'analysis' and data.get('baseline_id'):
                if data['baseline_id'] != self.report_id or not self.project:
                    raise AppError('Open the version you want to update, then try again.', 409)
                baseline = self.read_report(data['baseline_id'])
                data = {**data, 'project_name':baseline.get('provenance', {}).get('project_name') or Path(baseline['archive']).stem}
            if kind == 'analysis':
                if data.get('linked_files'):
                    linked = self.store.preference('linked_kicad_files')
                    linked_files = list(linked['files'].values()) + [str(Path(linked['path']) / name) for name in linked.get('sheets') or [] if name != linked.get('schematic')]
                    if not linked or any(not Path(file).is_file() for file in linked_files):
                        raise AppError('A connected KiCad file was moved or renamed. Select the files again.')
                    data = {**data, 'path': linked['path'], 'schematic': linked['schematic'],
                            'pcb': linked['pcb'], 'upload_id': None}
                project_name=data.get('project_name','')
                if not isinstance(project_name,str) or len(project_name)>120 or any(ord(c)<32 for c in project_name):
                    raise AppError('Use a project name under 120 characters, without control characters.')
                if data.get('cpl_units','auto') not in ('auto','mm','in'):
                    raise AppError('Choose auto, mm, or inches for placement units.')
                upload_id = data.get('upload_id')
                if upload_id:
                    if not self.upload or upload_id != self.upload['id']:
                        raise AppError('Upload the ZIP again; it is no longer available.')
                    source = self.upload['path']
                else:
                    raw = data.get('path', '')
                    if not isinstance(raw, str) or not raw.strip() or len(raw) > 4096 or '\x00' in raw:
                        raise AppError('Enter a project folder or ZIP path.')
                    source = Path(raw.strip().strip('"\'')).expanduser()
                    if not source.is_absolute():
                        source = Path(__file__).parent / source
                    if not source.is_dir() and not (source.is_file() and source.suffix.lower() == '.zip'):
                        raise AppError('Project not found. Enter an existing folder or ZIP path.')
                for name in ('pcb', 'schematic', 'bom', 'cpl'):
                    if not isinstance(data.get(name, ''), str) or len(data.get(name, '')) > 4096:
                        raise AppError('Invalid source file selection.')
            self.job = {'id': secrets.token_hex(8), 'kind': kind, 'status': 'running',
                        'message': 'Starting…', 'started': now(), 'events': []}
            self.live_pipeline = None
            self.cancel_event.clear()
            # Keep the last completed board visible while its next check runs.
            project = deepcopy(self.project) if kind in ('question', 'rethink', 'review') else None
            if project:
                project.design_context = context or self.store.design_context(context_key(project)) or project.design_context or empty_context()
                project.provenance['parent_report'] = self.report_id
            key, model = self.keys_for_ai(), self.model
            self.worker = threading.Thread(target=self._work,
                args=(kind, data, source, project, key, model, use_ai), daemon=True)
            self.worker.start()
            return self.snapshot()

    def cancel(self, data):
        with self.lock:
            if not self.job or data.get('job_id') != self.job['id']:
                raise AppError('This check is no longer active. Refresh its status.', 409)
            if self.job['status'] == 'running':
                self.cancel_event.set()
                self.job.update(cancel_requested=True,
                    message='Stopping after the current operation returns. Your previous report stays available.')
            return self.snapshot()

    def publish_live_pipeline(self, snapshot):
        """Store a partial investigation snapshot for the Command Center.
        Called from the worker thread; the endpoint serves it while running."""
        try:
            with self.lock:
                if self.job and self.job.get('status') == 'running':
                    self.live_pipeline = snapshot
        except Exception:
            pass

    def progress(self, message):
        message=re.sub(r'Asking (?:AI|Gemini|OpenAI)(?: · [^…]+)?', 'Asking Vector', message)
        message=message.replace('AI settings','Vector settings').replace('AI review','Vector review').replace('Checking AI','Checking Vector').replace('Receiving OpenAI','Receiving Vector')
        if self.cancel_event.is_set():
            raise JobCancelled()
        with self.lock:
            self.job['message'] = message
            self.job['events'].append({'message': message, 'at': now()})

    def _work(self, kind, data, source, project, key, model, use_ai):
        mode = self.usage_mode()
        action = 'connection' if kind == 'connection' else 'chat' if kind == 'question' else 'review'
        with request_budget(mode, 'connection' if kind == 'connection' else 'review') as budget, meta_action(action):
            try:
                self._work_bounded(kind, data, source, project, key, model, use_ai)
            finally:
                with self.lock:
                    self.job['request_usage'] = budget.summary()

    def _work_bounded(self, kind, data, source, project, key, model, use_ai):
        baseline = None
        try:
            input_source = self.store.preference('input:'+self.report_id) if kind in ('question', 'rethink', 'review') and self.report_id else None
            parent_id = self.report_id if kind in ('question', 'rethink', 'review') else data.get('baseline_id')
            baseline = self.read_report(parent_id) if kind == 'analysis' and parent_id else None
            if kind == 'connection':
                self.progress(f'Testing {model} with a small request…')
                result = check_ai_connection(key=key, model=model, progress=self.progress)
                if self.cancel_event.is_set():
                    raise JobCancelled()
                with self.lock:
                    self.connection_test = {'model': model, 'status': result['status'],
                        'message': result['message'], 'checked_at': now(),
                        'credential_revision': self._credential_revision}
                    self.job.update(status='completed' if result['status'] == 'completed' else 'failed',
                                    message=result['message'], result=result, ended=now())
                return
            if kind == 'analysis':
                input_source = {'upload_name':source.name} if data.get('upload_id') else {'path':str(source.resolve())}
                self.progress('Reading project files…')
                project = analyze_project(source, native=data.get('native', True) is not False,
                    pcb=data.get('pcb') or None, schematic=data.get('schematic') or None,
                    bom=data.get('bom') or None,cpl=data.get('cpl') or None,cpl_units=data.get('cpl_units','auto'),
                    kicad_cli=self.kicad_cli, progress=self.progress)
                if baseline:
                    old_uuid = baseline.get('provenance', {}).get('schematic_uuid')
                    if old_uuid and old_uuid != project.provenance.get('schematic_uuid'):
                        raise ValueError('These files belong to a different schematic, or the schematic could not be read. Open the previous run from Project library and choose the matching files, or run this as a new project.')
                    project.provenance['parent_report'] = parent_id
                project.provenance['project_name']=data.get('project_name','').strip() or Path(project.archive).stem
                project.design_context = self.store.design_context(context_key(project)) or empty_context()
                if 'product_context' in data and data['product_context'] != project.design_context.get('product_context', {}):
                    current = project.design_context
                    project.design_context = self.store.save_context(context_key(project),
                        edit_context(project, current, {'product_context': data['product_context']}), current['revision'])
                project.ai_review = {'status': 'running' if use_ai else 'disabled',
                                     'message': 'Vector review is running.' if use_ai else 'Vector review was turned off for this run.'}
            if kind == 'review':
                # Read saved assets before spending credits. A retry cannot depend
                # on rerunning KiCad, parts lookup or assembly export.
                self.progress('Reusing the saved checks, datasheets and product context…')
                from .fabrication import previous_fabrication
                project._fabrication_assets = previous_fabrication(self.reports/parent_id, project.fabrication)
                if project.fabrication.get('status') == 'draft' and 'fabrication-draft.zip' not in project._fabrication_assets:
                    raise ValueError('The saved fabrication package is missing or changed. Use Update review to regenerate it before retrying AI. No AI request was sent.')
                board_visual = previous_visual(self.reports/parent_id)
                schematic_visual = previous_schematic(self.reports/parent_id)
            if kind != 'review' and data.get('datasheets', kind == 'analysis') is True:
                self.progress('Finding LCSC parts and reading their datasheets…')
                project.datasheets = collect_datasheets(project, self.reports / '.datasheets', self.progress)
            if kind in ('analysis', 'rethink') and (project.datasheets or {}).get('documents'):
                # Re-quote on rethinks too: documents may have arrived after the
                # findings were first written, and attach is idempotent.
                from .checks.requirements import attach_datasheet_evidence
                quoted = attach_datasheet_evidence(project)
                if quoted:
                    self.progress(f'Quoted {quoted} retrieved requirement(s) into the findings…')
            project.verification = self.store.design_context('verification:' + context_key(project)) or project.verification
            copper_source = source or (input_source or {}).get('path')
            # A context-only rethink must not repay the copper/geometry bill
            # when the KiCad files are byte-identical to the reviewed run: the
            # deepcopied project already carries the verified results, and
            # source_status() re-hashes the files so a stale cache can never
            # pass as current. Any change, gap, or unreadable file recomputes.
            copper_current = False
            if kind == 'rethink' and copper_source:
                from . import file_fixes
                try:
                    copper_current = file_fixes.source_status(
                        copper_source, project.provenance.get('source_hashes', {})).get('status') == 'current'
                except (OSError, ValueError):
                    copper_current = False
            if kind == 'analysis' or (kind == 'rethink' and copper_source and not copper_current):
                from .signal_paths import analyze_signal_paths
                from .routing_analysis import analyze_routing
                from .parsers.sexpr import ParseError as _PcbParseError
                from .parsers.sexpr import parse as _parse_pcb_tree
                pcb_source = copper_source
                # One verified read+parse shared by both copper passes: the
                # digest check guarantees identical bytes, and neither pass
                # mutates the tree. Any failure falls back to per-pass parsing.
                pcb_raw, pcb_tree = None, None
                try:
                    pcb_relative = project.selected_files.get('pcb')
                    pcb_digest = project.provenance.get('source_hashes', {}).get(pcb_relative)
                    if pcb_relative and pcb_digest and pcb_source:
                        candidate = read_project_file(pcb_source, pcb_relative)
                        if hashlib.sha256(candidate).hexdigest() == pcb_digest:
                            pcb_raw, pcb_tree = candidate, _parse_pcb_tree(candidate.decode('utf-8'), 'kicad_pcb')
                except (ArchiveError, UnicodeError, _PcbParseError, OSError, ValueError):
                    pcb_raw, pcb_tree = None, None
                self.progress('Tracing antenna and sensitive input copper…')
                analyze_signal_paths(project, pcb_source, pcb_tree=pcb_tree, pcb_raw=pcb_raw)
                self.progress('Measuring routed copper lengths and pair geometry…')
                analyze_routing(project, pcb_source, pcb_tree=pcb_tree)
            elif kind == 'rethink' and copper_current:
                self.progress('KiCad files unchanged — reusing the verified routing, assembly and manufacturing results…')
            if kind != 'review' and not (kind == 'rethink' and copper_current):
                project.assembly_export = generate_assembly(project, source or (input_source or {}).get('path'),
                                                            self.kicad_cli, self.progress)
                project.manufacturing = self.manufacturing_result(project, parent_id if kind != 'analysis' else None,
                    source=source or (input_source or {}).get('path'), measure=True)
            awaiting_inputs=False
            if data.get('context_intake') is True:
                from .review_inputs import review_inputs
                from .verification import evaluate
                from .release_review import before_ordering
                input_design=asdict(project)
                input_design['verification_results']=evaluate(input_design)
                input_design['before_ordering']=before_ordering(input_design)
                from .user_profile import guided_questions
                awaiting_inputs=bool(guided_questions(project)['blocking_unanswered'] or review_inputs(input_design)['tasks'])
            if awaiting_inputs:
                project.ai_review = {'status':'needs_context', 'resume_ai':use_ai,
                    'message':'Files checked. Answer the component-specific questions before the Vector review.'}
                use_ai = False
            if use_ai:
                self.progress('Asking Vector…')
                deep = True
                if kind == 'review' and data.get('connections_only') is True:
                    from .ai.pin_review import run_connection_review
                    project.ai_review = run_connection_review(project,key=key,model=model,progress=self.progress)
                else:
                    project.ai_review = run_ai_review(project, key=key, model=model, question=data.get('question', ''), design=True, deep=deep, progress=self.progress, publish=self.publish_live_pipeline, datasheet_cache=str(self.reports / '.datasheets'))
            elif kind == 'rethink' and not awaiting_inputs:
                project.ai_review = {'status': 'not_configured' if not key else 'disabled',
                    'message': 'Context and local steps updated. Connect AI to generate a context-aware AI change plan.' if not key else 'Context and local steps updated; AI was not requested.'}
            if kind == 'analysis':
                self.progress('Drawing the board preview…')
                board_visual = render_board(project, source, self.kicad_cli)
                self.progress('Drawing the schematic preview…')
                schematic_visual = render_schematic(project, source, self.kicad_cli)
            elif parent_id and kind != 'review':
                from .fabrication import previous_fabrication
                project._fabrication_assets = previous_fabrication(self.reports/parent_id, project.fabrication)
                board_visual = previous_visual(self.reports/parent_id)
                schematic_visual = previous_schematic(self.reports/parent_id)
            elif kind != 'review':
                board_visual = None
                schematic_visual = None
            project.provenance.update(checker_version='0.6.0', schema_version=5)
            project.action_plan = compose_plan(project)
            if baseline:
                self.progress('Checking which issues cleared in your updated files…')
                project.provenance['revision_comparison'] = compare_runs(baseline, asdict(project)) | {'before':parent_id}
            self.progress('Saving your report…')
            # Adopt automatic question-text maintenance from the store so a
            # fresh report does not call itself stale. Genuine mid-run user
            # answers keep the run snapshot and still flag stale honestly.
            refresh_context_from_store(project, self.store)
            folder = save_report(project, self.reports, board_visual, schematic_visual)
            self.store.index(folder.name,asdict(project),mtime_ns=(folder/'report.json').stat().st_mtime_ns,
                             created=now())
            self.store.preference('input:'+folder.name,input_source or {})
            self.store.preference('last_report',folder.name)
            if parent_id:
                # A context-only rereview preserves the journal for unchanged finding evidence.
                old = self.store.decisions(parent_id)
                for group in keyed_groups(asdict(project)):
                    decision = old.get(group['key'])
                    if decision and decision['signature'] == group['signature']:
                        self.store.annotate(folder.name, group['key'], group['signature'], decision['status'], decision['note'])
            with self.lock:
                if self.cancel_event.is_set():
                    raise JobCancelled()
                # Publish the report, previews and findings as one revision.
                self.project = project
                self.report_id = folder.name
                self.revision += 1
                message = 'Review complete. Your report is saved.'
                if project.ai_review.get('status') == 'needs_context':
                    message = 'Files checked. A few external details are missing; answer the questions to continue.'
                if kind == 'rethink' and not use_ai and not awaiting_inputs:
                    message = 'Context, local change steps and datasheet results saved. Connect AI for AI reconsideration.' if not key else 'Context and local change steps saved.'
                elif project.ai_review.get('status') in ('failed', 'not_configured'):
                    message = 'Vector review did not finish: ' + project.ai_review.get('message', 'Open the review log for details.')
                elif project.ai_review.get('rejected_items'):
                    coverage = project.ai_review.get('coverage', {})
                    message = f"Local checks saved. AI explanations verified for {coverage.get('validated_actions', 0)} of {coverage.get('requested_actions', 0)} issues. The remaining issues still need review."
                elif project.ai_review.get('circuit_audit', {}).get('status') == 'partial':
                    audit = project.ai_review['circuit_audit']
                    message = f"Review saved. Circuit comparisons completed for {audit['reviewed_count']} of {audit['target_count']} active parts. Open Circuit → Circuit analysis for the remaining gaps."
                self.job.update(status='completed', message=message, ended=now(), ai_status=project.ai_review.get('status'),
                    needs_product_context=project.ai_review.get('status')=='needs_context')
        except JobCancelled:
            with self.lock:
                self.store.preference('last_report', self.report_id)
                self.job.update(status='cancelled', message='Check stopped. Your previous report is still available.', ended=now())
        except (ArchiveError, OSError, ValueError) as exc:
            message = redact_error(str(exc), key)
            with self.lock:
                if baseline:
                    self.project = restore_project(baseline)
                    self.report_id = parent_id
                    self.revision += 1
                self.job.update(status='failed', message=message, ended=now())
        except Exception:
            with self.lock:
                if baseline:
                    self.project = restore_project(baseline)
                    self.report_id = parent_id
                    self.revision += 1
                self.job.update(status='failed', message='The checker encountered an unexpected error. Any completed local results remain visible.', ended=now())

    def close(self):
        clear_usage_recorder(self.store.record_meta_usage)
        self.editor_connections.close()
        if self.keychain_thread and self.keychain_thread.is_alive():
            self.keychain_thread.join(timeout=1)
        if self.worker and self.worker.is_alive():
            self.worker.join()
        self.temp.cleanup()


def ai_error_response(exc):
    """HTTP body/status for an AIError raised by a route handler.

    AI failures reach the user with their real reason (missing key, output
    limit, timeout) instead of a generic 400/500. AIError messages never
    embed credentials.
    """
    kind = (exc.diagnostics or {}).get('error_kind')
    status = 429 if kind in ('busy', 'cooldown', 'request_budget') else 400
    return ({'error': str(exc), 'retryable': bool((exc.diagnostics or {}).get('retryable'))},
            status)


def file_picker_script(directory=None):
    """Native Mac file-picker script; opens at directory when it is a real folder."""
    script = ('set picked to choose file with prompt "Select the .kicad_pro, the .kicad_pcb, and every '
              '.kicad_sch sheet together (hold Command to select several files)"')
    if (isinstance(directory, str) and directory
            and all(c not in directory for c in ('\n', '"', '\x00')) and Path(directory).is_dir()):
        script += f' default location (POSIX file "{directory}")'
    return script + ''' with multiple selections allowed
set output to ""
repeat with itemPath in picked
set output to output & POSIX path of itemPath & linefeed
end repeat
return output'''


def picker_gui_available():
    """Fast probe: can this server process drive Mac dialogs at all?

    A UI-less `return` never opens anything, so a failure here proves the
    native picker cannot work — failing fast instead of hanging two minutes
    on a dialog that will never appear.
    """
    try:
        probe = subprocess.run(['osascript', '-e', 'return "ok"'],
                               capture_output=True, text=True, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def picker_paths(script, *, what='file'):
    """Run a native Mac picker and return the chosen POSIX paths.

    Returns None only when the user genuinely cancels (AppleScript -128).
    Any other failure raises AppError with the real reason: previously every
    osascript failure silently looked like a cancel, so a dead picker button
    showed nothing at all.
    """
    if not picker_gui_available():
        raise AppError(f'This server cannot open the Mac {what} picker (no dialog access). '
                       'Type the folder path into Project location instead, or start the app directly on your Mac.')
    try:
        result = subprocess.run(['osascript', '-e', script],
                                capture_output=True, text=True, timeout=120, check=False)
    except OSError as exc:
        raise AppError(f'The Mac {what} picker could not open ({type(exc).__name__}). '
                       'Type the path into Project location instead.') from None
    except subprocess.TimeoutExpired:
        raise AppError(f'The Mac {what} picker timed out waiting. Press the link button again when ready '
                       'to pick, or type the folder path into Project location instead.') from None
    if result.returncode:
        detail = (result.stderr or '').strip().replace('\n', ' ')
        if '-128' in detail or 'User canceled' in detail:
            return None
        raise AppError(f'The Mac {what} picker could not open. '
                       'Type the path into Project location instead.'
                       + (f' ({detail[:200]})' if detail else ''))
    return [line for line in result.stdout.splitlines() if line.strip()]


def resolve_root_schematic(entries, pro_stem):
    """Pick the top-level sheet name from [(name, text)] in one folder.

    Prefers the sheet matching the project stem, then a lone sheet, then the
    sheet no other sheet references (the hierarchy root). Raises AppError
    only when genuinely ambiguous.
    """
    names = [name for name, _ in entries]
    root = pro_stem + '.kicad_sch'
    if root in names:
        return root
    if len(names) == 1:
        return names[0]
    from .parsers.kicad_schematic import child_sheet_files
    from .parsers.sexpr import ParseError
    referenced = set()
    for _, text in entries:
        try:
            referenced.update(child_sheet_files(text))
        except (ParseError, ValueError):
            continue
    candidates = [name for name in names if name not in referenced]
    if len(candidates) == 1:
        return candidates[0]
    raise AppError('The top-level schematic is ambiguous. Select it with Select KiCad project files.')


def interpret_scan_route(state, project_id, data):
    """POST /api/livescan/projects/<id>/interpret. Module-level for testing."""
    from .breadboard_to_PCB.livescan_vision import interpret as interpret_scan
    from .ai.reviewer import AIError as VisionAIError
    name = data.get('image', '')
    try:
        body, _mime = livescan_store.load_image(state.reports, project_id, name)
    except ValueError as exc:
        raise AppError(str(exc), 404) from None
    if not state.meta_key:
        raise AppError('Add your Meta API key in Vector settings.', 400)
    try:
        return interpret_scan(
            project_id=project_id, image_bytes=body,
            suffix='.' + str(name).rsplit('.', 1)[-1], key=state.meta_key,
            view_hint=data.get('view_hint', ''), known=data.get('known', ''),
            manual=bool(data.get('manual')))
    except VisionAIError as exc:
        kind = (exc.diagnostics or {}).get('error_kind')
        status = 429 if kind in ('busy', 'cooldown', 'request_budget') else 502
        raise AppError(str(exc), status) from None


def interpret_batch_route(state, project_id, data):
    """POST /api/livescan/projects/<id>/interpret_batch. Module-level for testing."""
    from .breadboard_to_PCB.livescan_vision import interpret_batch as interpret_batch_scan
    from .ai.reviewer import AIError as VisionAIError
    names = data.get('images') or []
    if not isinstance(names, list) or not names:
        raise AppError('Select at least one photo.', 400)
    if not state.meta_key:
        raise AppError('Add your Meta API key in Vector settings.', 400)
    hints = data.get('view_hints') if isinstance(data.get('view_hints'), dict) else {}
    dims = data.get('dims') if isinstance(data.get('dims'), dict) else {}
    photos = []
    for raw_name in names[:8]:
        name = str(raw_name)
        try:
            body, _mime = livescan_store.load_image(state.reports, project_id, name)
        except ValueError as exc:
            photos.append({'name': name, 'bytes': b'', 'suffix': '',
                           'load_error': str(exc)})
            continue
        wh = dims.get(name) if isinstance(dims.get(name), list) else []
        photos.append({'name': name, 'bytes': body,
                       'suffix': '.' + name.rsplit('.', 1)[-1] if '.' in name else '',
                       'view_hint': str(hints.get(name, '')),
                       'w': wh[0] if len(wh) > 0 else 0, 'h': wh[1] if len(wh) > 1 else 0})
    for p in photos:
        if p.get('load_error'):
            p['bytes'] = b''
    try:
        known = data.get('known', '') if isinstance(data.get('known'), str) else ''
        out = interpret_batch_scan(project_id=project_id, photos=photos, key=state.meta_key,
                                   known=known[:1500])
    except VisionAIError as exc:
        kind = (exc.diagnostics or {}).get('error_kind')
        status = 429 if kind in ('busy', 'cooldown', 'request_budget') else 502
        raise AppError(str(exc), status) from None
    for p in photos:
        if p.get('load_error') and not any(e['photo'] == p['name'] for e in out['errors']):
            out['errors'].append({'photo': p['name'], 'error': p['load_error']})
    return out


def create_server(state, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(60)

        def log_message(self, format, *args):
            pass  # Never log request bodies or keys.

        def check_origin(self, write=False):
            port = self.server.server_address[1]
            hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin')
            if host not in hosts or (origin and origin != f'http://{host}'):
                raise AppError('Only this local app can access the checker.', 403)
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise AppError('Cross-site requests are not allowed.', 403)
            if write and not secrets.compare_digest(self.headers.get('X-PCB-Token', ''), state.token):
                raise AppError('Refresh this page before trying again.', 403)

        def send_bytes(self, body, content_type, status=200, filename=None, cache=False):
            compressed = (len(body) > 4096 and 'gzip' in self.headers.get('Accept-Encoding', '')
                          and ('json' in content_type or 'svg' in content_type or content_type.startswith('text/')))
            if compressed:
                body = gzip.compress(body, compresslevel=3)
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'private, max-age=3600' if cache else 'no-store')
            self.send_header('Vary', 'Accept-Encoding')
            if compressed:
                self.send_header('Content-Encoding', 'gzip')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def json(self, value, status=200):
            self.send_bytes(json.dumps(value).encode(), 'application/json; charset=utf-8', status)

        def unexpected_error(self, exc, where=''):
            # Exception messages can contain credentials or project text. The
            # browser only gets the class and a reportable reference. The
            # local console (never the browser) also gets the full traceback
            # with any key material redacted, so a developer can diagnose the
            # failure from the terminal without leaking secrets. Retrying the
            # same action is safe: no endpoint edits the user's KiCad files
            # without an explicit approve/apply step.
            incident = state._record_incident(where or self.path, exc, method=self.command)
            print(f'Local API error {incident} at {where or self.path}: {type(exc).__name__}', file=sys.stderr)
            try:
                from .ai.reviewer import redact_error as _redact
                print(_redact(traceback.format_exc(),
                              [getattr(state, 'key', ''), getattr(state, 'meta_key', ''),
                               getattr(state, 'openai_key', '')]), file=sys.stderr)
            except Exception:
                pass
            self.json({'error': f'The app could not complete this request (reference {incident}). Your KiCad files were not edited; retry the same action once. If it persists, report the reference.', 'incident': incident}, 500)

        def body_length(self, limit):
            try:
                size = int(self.headers.get('Content-Length', '-1'))
            except ValueError:
                raise AppError('Invalid request length.') from None
            if size < 0 or self.headers.get('Transfer-Encoding'):
                raise AppError('A request length is required.', 411)
            if size > limit:
                raise AppError('Upload exceeds the size limit.', 413)
            return size

        def do_GET(self):
            try:
                self.check_origin()
                path = urlsplit(self.path).path
                if path == '/preflight-logo.png':
                    self.send_bytes((ASSETS/'preflight-logo-header.png').read_bytes(),'image/png')
                elif path in ('/', '/app.css', '/refresh.css', '/app.js', '/editor-connections.js', '/product-context.js', '/workflow.js', '/design.js', '/file_fixes.js', '/fix_design.js', '/firmware.js', '/overview.js', '/manufacturing.js', '/release.js', '/locations.js', '/usage.js', '/command', '/command.js', '/command.css', '/command-replay.json', '/breadboard.js', '/scanmath.js', '/livetwin.js', '/livescan.js', '/livescan.css', '/vendor/three.module.js'):
                    entry = {'/': ('index.html', 'text/html'), '/app.css': ('app.css', 'text/css'),
                                  '/command': ('command.html', 'text/html'), '/command.js': ('command.js', 'text/javascript'),
                                  '/command.css': ('command.css', 'text/css'), '/command-replay.json': ('command-replay.json', 'application/json'),
                                  '/refresh.css': ('refresh.css', 'text/css'), '/overview.js': ('overview.js', 'text/javascript'),
                                  '/app.js': ('app.js', 'text/javascript'), '/workflow.js': ('workflow.js','text/javascript'),
                                  '/editor-connections.js': ('editor-connections.js', 'text/javascript'),
                                  '/product-context.js': ('product-context.js', 'text/javascript'),
                                  '/design.js': ('design.js', 'text/javascript'),
                                  '/file_fixes.js': ('file_fixes.js', 'text/javascript'),
                                  '/fix_design.js': ('fix_design.js', 'text/javascript'),
                                  '/firmware.js': ('firmware.js', 'text/javascript'),
                                  '/manufacturing.js': ('manufacturing.js', 'text/javascript'),
                                  '/release.js': ('release.js', 'text/javascript'), '/locations.js': ('locations.js', 'text/javascript'),
                                  '/usage.js': ('usage.js', 'text/javascript'),
                                  '/breadboard.js': ('breadboard.js', 'text/javascript', True),
                                  '/scanmath.js': ('scanmath.js', 'text/javascript', True),
                                  '/livetwin.js': ('livetwin.js', 'text/javascript', True),
                                  '/livescan.js': ('livescan.js', 'text/javascript', True),
                                  '/livescan.css': ('livescan.css', 'text/css', True),
                                  '/vendor/three.module.js': ('vendor/three.module.js', 'text/javascript')}[path]
                    name, mime = entry[0], entry[1]
                    base = BB_ASSETS if len(entry) > 2 else ASSETS
                    body = (base/name).read_bytes()
                    if name.endswith('.html'):
                        body = _cache_busted_html(body)
                    self.send_bytes(body, mime + '; charset=utf-8')
                elif path == '/api/investigation':
                    self.json(state.investigation_data())
                elif path == '/api/livescan/projects':
                    try:
                        self.json({'projects': livescan_store.list_projects(state.reports)})
                    except ValueError as exc:
                        raise AppError(str(exc), 400) from None
                elif path.startswith('/api/livescan/projects/'):
                    rest = path.removeprefix('/api/livescan/projects/')
                    try:
                        if '/' not in rest:
                            self.json(livescan_store.load_project(state.reports, unquote(rest)))
                        elif rest.count('/') == 2 and rest.split('/')[1] == 'images':
                            pid, _, name = rest.partition('/images/')
                            body, mime = livescan_store.load_image(state.reports, unquote(pid), unquote(name))
                            self.send_bytes(body, mime, cache=True)
                        else:
                            raise AppError('Not found.', 404)
                    except ValueError as exc:
                        message = str(exc)
                        raise AppError(message, 404 if 'nknown' in message or 'not found' in message else 400) from None
                elif path == '/api/health':
                    self.json({'app': 'pcb-preflight', 'workspace': str(ASSETS.parent.resolve())})
                elif path == '/api/config':
                    self.json(state.config())
                elif path == '/api/editor-connections':
                    self.json(state.editor_status())
                elif path == '/api/demo-projects':
                    self.json(state.demo_projects())
                elif path.startswith('/api/file-fix/'):
                    self.json(state.file_fix(path.removeprefix('/api/file-fix/')))
                elif path.startswith('/api/file-fix-preview/'):
                    parts=path.removeprefix('/api/file-fix-preview/').split('/')
                    if len(parts)!=2:raise AppError('Invalid preview.',404)
                    record=state.file_fix(parts[0]);name=parts[1]
                    allowed_previews=('before-front.svg','after-front.svg','before-back.svg','after-back.svg',
                                      'before-schematic.svg','after-schematic.svg')
                    if name not in record.get('previews',[]) or name not in allowed_previews:
                        raise AppError('Preview is unavailable.',404)
                    self.send_bytes((state.reports/'.file-fixes'/record['id']/name).read_bytes(),'image/svg+xml')
                elif path.startswith('/api/file-fix-diff/'):
                    record = state.file_fix(path.removeprefix('/api/file-fix-diff/'))
                    if record['status'] not in ('ready','applied','undone'):raise AppError('The preview is not ready.')
                    self.send_bytes((state.reports/'.file-fixes'/record['id']/'changes.diff').read_bytes(),
                                    'text/plain; charset=utf-8', filename='pcb-file-change.diff')
                elif path.startswith('/api/fix-design-export/'):
                    archive, files = state.export_fix_design(path.removeprefix('/api/fix-design-export/'))
                    self.send_bytes(archive, 'application/zip',
                                    filename='pcb-preflight-coordinated-fix.zip')
                elif path.startswith('/api/firmware-export/'):
                    archive, filename = state.export_firmware(unquote(path.removeprefix('/api/firmware-export/')))
                    self.send_bytes(archive, 'application/zip', filename=filename)
                elif path.startswith('/api/firmware-builds/'):
                    self.json(state.list_firmware_builds(unquote(path.removeprefix('/api/firmware-builds/'))))
                elif path.startswith('/api/firmware/'):
                    self.json(state.firmware(path.removeprefix('/api/firmware/')))
                elif path.startswith('/api/fix-designs/'):
                    self.json(state.list_fix_designs(unquote(path.removeprefix('/api/fix-designs/'))))
                elif path.startswith('/api/fix-design/'):
                    self.json(state.fix_design(path.removeprefix('/api/fix-design/')))
                elif path == '/api/state':
                    self.json(state.snapshot())
                elif path == '/api/project':
                    self.json(state.project_data())
                elif path == '/api/history':
                    self.json(state.history())
                elif path == '/api/library':
                    params=parse_qs(urlsplit(self.path).query)
                    try:
                        data = state.store.library(query=params.get('q',[''])[0],project=params.get('project',[''])[0],
                            offset=int(params.get('offset',['0'])[0]),limit=int(params.get('limit',['30'])[0]),
                            latest=params.get('latest',['0'])[0]=='1')
                        # Preview availability per run: the frontend only
                        # requests board-front.svg when it exists, so legacy
                        # runs without the export never fire a 404 (and the
                        # console stays clean). Store stays pure; this is a
                        # presentation annotation at the HTTP layer.
                        for it in data.get('items', []):
                            try:
                                state.report_path(it.get('id') or '', 'board-front.svg')
                                it['has_preview'] = True
                            except AppError:
                                it['has_preview'] = False
                        self.json(data)
                    except ValueError as exc:
                        raise AppError(str(exc)) from None
                elif path.startswith('/api/bundle/'):
                    self.send_bytes(state.bundle(unquote(path.removeprefix('/api/bundle/'))),'application/zip',filename='pcb-preflight-review.zip')
                elif path.startswith('/api/checklist/'):
                    from .release_review import checklist_text
                    report_id = unquote(path.removeprefix('/api/checklist/'))
                    with state.lock:
                        data = state.project_data() if report_id == state.report_id else state.read_report(report_id)
                    self.send_bytes(checklist_text(data, report_id),'text/plain; charset=utf-8',filename='before-ordering.txt')
                elif path.startswith('/proof-images/'):
                    filename=path.removeprefix('/proof-images/')
                    if not re.fullmatch(r'[0-9a-f]{64}\.png',filename):raise AppError('Image not found.',404)
                    file=state.reports/'.proofs'/filename
                    if file.is_symlink() or not file.is_file() or file.stat().st_size>16*1024*1024:raise AppError('Image not found.',404)
                    self.send_bytes(file.read_bytes(),'image/png',cache=True)
                elif path.startswith('/reports/'):
                    parts = path.split('/')
                    if len(parts) != 4:
                        raise AppError('Report not found.', 404)
                    filename = unquote(parts[3])
                    file = state.report_path(unquote(parts[2]), filename)
                    if filename.endswith('.svg'):
                        self.send_bytes(file.read_bytes(), 'image/svg+xml; charset=utf-8',cache=True)
                    else:
                        self.send_bytes(file.read_bytes(), 'application/octet-stream', filename=filename)
                else:
                    raise AppError('Not found.', 404)
            except AppError as exc:
                self.json({'error': str(exc)}, exc.status)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except AIError as exc:
                body, status = ai_error_response(exc)
                self.json(body, status)
            except sqlite3.Error:
                self.json({'error':'The app is running, but cannot access its saved project data. Check available disk space. The connection will retry automatically.', 'retryable':True},503)
            except OSError:
                self.json({'error': 'The requested file could not be read.'}, 404)
            except Exception as exc:
                self.unexpected_error(exc)

        def upload_zip(self):
            size = self.body_length(MAX_ARCHIVE_BYTES)
            name = unquote(self.headers.get('X-Filename', 'project.zip'))
            if not name or len(name) > 180 or '/' in name or '\\' in name or any(ord(c) < 32 for c in name) or not name.lower().endswith('.zip'):
                raise AppError('Choose a ZIP archive.')
            with state.lock:
                state.idle_required()
                state.uploading = True
            path = None
            try:
                upload_id = secrets.token_hex(12)
                folder = Path(state.temp.name) / upload_id
                folder.mkdir()
                path = folder / name
                remaining = size
                with path.open('xb') as target:
                    while remaining:
                        chunk = self.rfile.read(min(65536, remaining))
                        if not chunk:
                            raise AppError('Upload was interrupted. Please try again.')
                        target.write(chunk)
                        remaining -= len(chunk)
                with state.lock:
                    previous = state.upload
                    state.upload = {'id': upload_id, 'path': path}
                    if previous:
                        previous['path'].unlink(missing_ok=True)
                        previous['path'].parent.rmdir()
                self.json({'id': upload_id, 'name': name, 'size': size})
            except Exception:
                if path and (not state.upload or state.upload['path'] != path):
                    path.unlink(missing_ok=True)
                    path.parent.rmdir()
                raise
            finally:
                with state.lock:
                    state.uploading = False

        def do_POST(self):
            try:
                self.check_origin(write=True)
                path = urlsplit(self.path).path
                if path == '/api/upload':
                    self.upload_zip()
                    return
                if path.startswith('/api/livescan/projects/') and path.endswith('/images'):
                    from .breadboard_to_PCB.livescan import MAX_IMAGE_BYTES
                    project_id = unquote(path[len('/api/livescan/projects/'):-len('/images')])
                    size = self.body_length(MAX_IMAGE_BYTES)
                    name = unquote(self.headers.get('X-Filename', 'capture.png'))
                    body = self.rfile.read(size)
                    try:
                        self.json(livescan_store.save_image(state.reports, project_id, name, body))
                    except ValueError as exc:
                        message = str(exc)
                        raise AppError(message, 404 if 'nknown' in message or 'not found' in message else 400) from None
                    return
                size = self.body_length(40 * 1024 * 1024 if path == '/api/upload-project-files' else 2 * 1024 * 1024 if path.startswith('/api/livescan/') else 64 * 1024)
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise AppError('Expected JSON input.', 415)
                try:
                    data = json.loads(self.rfile.read(size))
                    if not isinstance(data, dict):
                        raise ValueError()
                except (ValueError, UnicodeError):
                    raise AppError('Invalid JSON input.') from None
                if path == '/api/settings':
                    self.json(state.settings(data))
                elif path == '/api/editor-connect':
                    self.json(state.connect_editor(data))
                elif path == '/api/editor-fusion-setup':
                    try:
                        self.json(state.editor_connections.prepare_fusion())
                    except EditorConnectionError as exc:
                        raise AppError(str(exc)) from None
                elif path == '/api/editor-disconnect':
                    try:
                        state.editor_connections.disconnect(data.get('id'))
                    except EditorConnectionError as exc:
                        raise AppError(str(exc)) from None
                    self.json({'disconnected': True})
                elif path == '/api/editor-use':
                    self.json(state.use_editor_connection(data.get('id')))
                elif path == '/api/choose-local-editor-files':
                    editor = data.get('editor')
                    if not isinstance(editor, str) or editor not in EDITORS:
                        raise AppError('Choose an editor first.')
                    if sys.platform != 'darwin':
                        raise AppError('Enter the original file paths below, one per line.')
                    picked = picker_paths('''set picked to choose file with prompt "Select the original project, schematic sheets and PCB saved by your editor" with multiple selections allowed
set output to ""
repeat with itemPath in picked
set output to output & POSIX path of itemPath & linefeed
end repeat
return output''', what='file')
                    if picked is None:
                        self.json({'cancelled': True})
                    else:
                        self.json(state.connect_editor({'editor': editor, 'files': picked}))
                elif path == '/api/check-scope':
                    self.json(state.save_check_scope(data))
                elif path == '/api/product-questions':
                    self.json(state.product_answers(data))
                elif path == '/api/product-answers':
                    self.json(state.product_answers(data,save=True))
                elif path == '/api/question-answer':
                    self.json(state.save_question_answer(data))
                elif path == '/api/choose-local-files':
                    if sys.platform != 'darwin':
                        raise AppError('Use the project folder path on this computer.')
                    script = file_picker_script(data.get('directory'))
                    picked = picker_paths(script, what='file')
                    if picked is None:
                        self.json({'cancelled': True})
                    else:
                        self.json(state.link_local_files(picked))
                elif path == '/api/choose-local-folder':
                    if sys.platform != 'darwin':
                        raise AppError('Enter the folder path in Project location on this computer.')
                    picked = picker_paths('POSIX path of (choose folder with prompt "Choose the project folder you open in KiCad")', what='folder')
                    if picked is None:
                        self.json({'cancelled': True})
                    else:
                        folder = Path(picked[0]).resolve()
                        if not folder.is_dir() or not list(folder.glob('*.kicad_pcb')) or not list(folder.glob('*.kicad_sch')):
                            raise AppError('Choose the folder containing your .kicad_sch and .kicad_pcb files.')
                        self.json({'path': str(folder), 'name': folder.name})
                elif path == '/api/link-local-paths':
                    self.json(state.link_local_names(data.get('directory'), data.get('names')))
                elif path == '/api/demo-projects':
                    self.json(state.demo_projects())
                elif path == '/api/demo-open':
                    self.json(state.open_demo_project(data.get('name')))
                elif path == '/api/demo-reset':
                    self.json(state.reset_demo_project(data.get('name')))
                elif path == '/api/demo-open-kicad':
                    self.json(state.open_demo_in_kicad(data.get('name')))
                elif path == '/api/project-context':
                    self.json(state.project_context(data))
                elif path == '/api/upload-project-files':
                    self.json(state.upload_project_files(data))
                elif path == '/api/open-report':
                    self.json(state.open_report(data.get('id', '')))
                elif path == '/api/decision':
                    self.json(state.annotate(data))
                elif path == '/api/issue-proof':
                    self.json(state.proof(data))
                elif path == '/api/schematic-preview':
                    self.json(state.schematic_preview(data))
                elif path == '/api/manufacturing':
                    self.json(state.select_manufacturer(data))
                elif path == '/api/verification':
                    self.json(state.save_verification(data))
                elif path == '/api/engineering-geometry':
                    self.json(state.measure_engineering_geometry(data))
                elif path == '/api/chat':
                    self.json(state.project_chat(data))
                elif path == '/api/step-help':
                    self.json(state.step_help(data))
                elif path == '/api/file-fix/prepare':
                    self.json(state.prepare_file_fix(data), 202)
                elif path == '/api/file-fix/continue':
                    self.json(state.continue_file_fix(data),202)
                elif path == '/api/file-fix/apply':
                    self.json(state.apply_file_fix(data))
                elif path == '/api/file-fix/undo':
                    self.json(state.apply_file_fix(data, undo=True))
                elif path == '/api/firmware/prepare':
                    self.json(state.prepare_firmware(data), 202)
                elif path == '/api/firmware/plan':
                    self.json(state.plan_firmware(data))
                elif path == '/api/firmware/answer':
                    self.json(state.answer_firmware(data))
                elif path == '/api/firmware/generate':
                    self.json(state.generate_firmware(data))
                elif path == '/api/firmware/compile':
                    self.json(state.compile_firmware(data))
                elif path == '/api/firmware/artifact':
                    body, content_type, filename = state.firmware_artifact(data)
                    self.send_bytes(body, content_type, filename=filename)
                elif path == '/api/fix-design/prepare':
                    self.json(state.prepare_fix_design(data), 202)
                elif path == '/api/fix-design/reconnect':
                    self.json(state.reconnect_fix_design(data))
                elif path == '/api/fix-design/approve':
                    self.json(state.approve_fix_design(data))
                elif path == '/api/fix-design/reject':
                    self.json(state.reject_fix_design(data))
                elif path == '/api/cancel':
                    self.json(state.cancel(data))
                elif path == '/api/compare':
                    self.json(state.compare(data))
                elif path == '/api/library/refresh':
                    state.sync_history()
                    self.json({'status':'indexed'})
                elif path == '/api/usage-budget':
                    self.json(state.save_usage_budget(data))
                elif path == '/api/livescan/projects':
                    name = data.get('name', '')
                    size = data.get('size', 'full')
                    if not isinstance(name, str) or len(name) > 120:
                        raise AppError('Invalid project name.')
                    if size not in ('full', 'half'):
                        raise AppError('Unknown breadboard size.')
                    project_id = livescan_store.new_id()
                    from datetime import datetime, timezone as tz
                    now = datetime.now(tz.utc).isoformat()
                    project = {'version': 1, 'id': project_id, 'name': name.strip() or 'Breadboard build',
                               'created': now, 'updated': now,
                               'breadboard': {'size': size, 'railGaps': {}},
                               'intent': {'text': '', 'fields': {}, 'updated': None},
                               'components': [], 'wires': [], 'images': [], 'questions': [],
                               'confirmations': {}, 'counters': {}}
                    livescan_store.save_project(state.reports, project_id, project)
                    self.json(project, 201)
                elif path.startswith('/api/livescan/projects/'):
                    rest = path.removeprefix('/api/livescan/projects/')
                    try:
                        if rest.endswith('/save') and '/' not in rest[:-5]:
                            project_id = unquote(rest[:-5])
                            project = data.get('project')
                            self.json(livescan_store.save_project(state.reports, project_id, project))
                        elif rest.endswith('/delete') and '/' not in rest[:-7]:
                            self.json(livescan_store.delete_project(state.reports, unquote(rest[:-7])))
                        elif rest.endswith('/interpret') and '/' not in rest[:-10]:
                            self.json(interpret_scan_route(
                                state, unquote(rest[:-10]), data))
                        elif rest.endswith('/interpret_batch') and '/' not in rest[:-16]:
                            self.json(interpret_batch_route(
                                state, unquote(rest[:-16]), data))
                        else:
                            raise AppError('Not found.', 404)
                    except ValueError as exc:
                        message = str(exc)
                        raise AppError(message, 404 if 'nknown' in message or 'not found' in message else 400) from None
                elif path in ('/api/run', '/api/ask', '/api/test-ai', '/api/rethink', '/api/review-ai'):
                    kind = {'/api/run': 'analysis', '/api/ask': 'question', '/api/test-ai': 'connection', '/api/rethink': 'rethink', '/api/review-ai':'review'}[path]
                    self.json(state.start(kind, data), 202)
                else:
                    raise AppError('Not found.', 404)
            except AppError as exc:
                self.json({'error': str(exc)}, exc.status)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except AIError as exc:
                body, status = ai_error_response(exc)
                self.json(body, status)
            except sqlite3.Error:
                self.json({'error':'The app cannot access its saved project data. Check available disk space, then retry your action.', 'retryable':True},503)
            except (OSError, ValueError, TypeError):
                self.json({'error': 'The request could not be processed. Check your input and try again.'}, 400)
            except Exception as exc:
                self.unexpected_error(exc)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def serve(default_path, reports, *, port=8765, open_browser=True, key='', model=None, kicad_cli=None, ai=None, native=True):
    warning = disk_warning(reports)
    if warning:
        print(warning, flush=True)
    state = AppState(default_path, reports, key=key, model=model, kicad_cli=kicad_cli, ai=ai, native=native,
                     adopt_keychain=True)
    try:
        server = create_server(state, port)
    except OSError:
        state.close()
        print(f'Could not start the app on port {port}. Try --port {port + 1}.')
        return 2
    url = f'http://127.0.0.1:{server.server_address[1]}'
    print(f'PCB Preflight is ready: {url}\nKeep this Terminal open. Press Ctrl+C to stop.', flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print('\nStopping the app. Finishing any active check before cleanup…', flush=True)
    finally:
        server.server_close()
        state.close()
    return 0
