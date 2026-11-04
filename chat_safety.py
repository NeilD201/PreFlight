"""Local chat boundaries: credentials, concurrent spending and evidence age."""
from functools import wraps
from pathlib import Path
import re


def guarded_chat(method):
    @wraps(method)
    def call(state, data):
        from .webapp import AppError
        # One pending reply per conversation, across browser tabs. Different
        # issue chats can run concurrently up to a small workspace-wide bound.
        channel=(method.__name__,str(data.get('report_id')),str(data.get('group_id','')))
        with state.lock:
            if channel in state.chat_requests:
                raise AppError('An answer is already being prepared for this conversation. Wait for it before sending again.',409)
            if len(state.chat_requests)>=2:
                raise AppError('Two chat answers are already running. Wait for one to finish.',429)
            text=str(data.get('question',''))+' '+str(data.get('history',[]))
            keys=[state.openai_key,getattr(state,'meta_key',''),*state.gemini_keys()]
            if (any(len(k)>=12 and k in text for k in keys if k)
                    or re.search(r'\b(?:sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,})',text)):
                raise AppError('This message appears to contain an API key. Remove it from the message and use Vector settings for keys.')
            state.chat_requests.add(channel)
        try:
            return method(state,data)
        finally:
            with state.lock:state.chat_requests.discard(channel)
    return call


def evidence_state(project, source, review_context_revision, current_context_revision=None):
    from .file_fixes import source_status
    if source and Path(source).suffix.lower()=='.zip':
        status={'status':'snapshot','changed':[],'missing':[]}
    else:
        status=source_status(source,project.provenance.get('source_hashes',{}))
    context_revision=project.design_context.get('revision',0) if current_context_revision is None else current_context_revision
    context_changed=context_revision!=review_context_revision
    warning=''
    if status['status']=='changed':
        warning='Your KiCad files changed since this review. This answer describes the saved version; update the review before using its edit advice.'
    elif context_changed:
        warning='Your product answers changed since this review. Existing findings need a new review against those answers.'
    elif status['status']=='unavailable':
        warning='This answer uses the saved review. The app could not verify whether the source files are still current.'
    return {**status,'context_changed':context_changed,'warning':warning,
            'basis':'Saved schematic and PCB evidence; file freshness is not verification of hardware behavior'}


EVIDENCE_INSTRUCTIONS = '''Read evidence_state before giving board-specific advice. If files have
changed, say you are explaining the saved revision and ask for Update review before making
current-board edit recommendations. New product answers do not revalidate old findings.
If freshness is unavailable or snapshot-only, never claim to have inspected live KiCad files.
Connection neighborhoods show saved net membership, not simulated current flow, signal quality,
or proof of copper continuity. Do not join arbitrary pins through an IC. Explicitly account
for omitted nets or endpoints before making claims about the entire circuit.
'''
