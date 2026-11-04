"""Keep investigation work separate from actions the user can carry out."""
import re

INSTRUCTIONS = '''Preflight owns the analysis. Do not give the user homework to read a
datasheet, identify saved pins, inspect routing, calculate a value from supplied data,
compare the BOM with the PCB, or decide whether a warning is a fault. Perform the
comparison using the available evidence and tools. State the result and its basis.
User actions are supported changes, explicit approval of a prepared edit, selecting
an ordering option, saving changed files and updating the review. Follow-up questions
are for unavailable product intent or operating facts only. Reuse saved answers.
If a needed document, measurement, simulation or editor capability is unavailable,
name that specific analysis limit. Do not turn it into a generic user inspection task,
invent the missing result, or mark the check complete. A request to run Preflight again
after the user saves an edit is appropriate; asking the user to assess the result is not.
Separate a documented design conclusion from unverified physical performance.
When the user explicitly asks how to measure or inspect something, explain that procedure.
'''


def delegated_check(step):
    """Recognize explicit investigation tasks, without matching product questions.

    Used for old/local advice too. Removing homework never completes its analysis.
    Concrete edits, order settings and approval steps are intentionally untouched.
    """
    text = str(step).strip()
    start = r'^(?:(?:please|first|then)\s+)?(?:check|inspect|review|read|verify|compare|look at|follow|find|identify)\b'
    subject = r'\b(?:datasheet|linked part information|pin(?:s|out| function)?|routing|return path|tracks?|copper|gap|parts list|placement file|schematic|original warning|board or schematic|signal on a real board|package drawing)\b'
    return bool(re.search(start,text,re.I) and re.search(subject,text,re.I))


def separate_analysis(action):
    steps = action.get('steps', [])
    pending = [s for s in steps if delegated_check(s)]
    if not pending:
        return
    # Later steps may depend on the result of the delegated check. Preserve the
    # whole original plan as pending rather than displaying a conditional guess.
    action['pending_analysis'] = list(dict.fromkeys(action.get('pending_analysis', []) + steps))
    action['steps'] = []
    action['analysis_limit'] = 'Preflight has not completed the comparison behind this advice. No conclusion is established for that part of the analysis.'
    if action.get('disposition') == 'change':
        # Dependent edits cannot be justified by an investigation still assigned
        # to the user. Retain them for diagnosis, not as an approved change plan.
        action['disposition'] = 'investigate'
