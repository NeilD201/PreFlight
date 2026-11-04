"""Shared writing style for project and issue conversations."""
from .explanation_profile import EXPLANATION_INSTRUCTIONS
from .checking_policy import INSTRUCTIONS as CHECKING_INSTRUCTIONS

CHAT_STYLE = '''Response style: answer the user's exact question in the first sentence.
Default to 40–90 words, with at most 120 words unless the user asks for detail, a full
procedure or a longer artifact. A simple question may need only one sentence.
Use one short paragraph, or the direct answer followed by up to three short bullets.
Use numbered steps only for an actual procedure. Each step should say one concrete action.
Use everyday English. Explain an unfamiliar term briefly only when needed for this answer.
Do not recap the board, repeat the question, add a generic introduction, enumerate unrelated
issues, or end with an unsolicited offer to explain more. Do not turn a narrow follow-up
into a full review. Keep necessary references, values, citations and important uncertainty.
For a yes/no question, answer yes, no, or what is unknown first, then give the reason.
For a fix question, say what to change and why. If no change is established, say that directly.
Keep all supplied context available for reasoning; brevity applies to the reply, not the analysis.
When the user explicitly requests detail, give the requested depth with clear short sections.
'''
CHAT_STYLE += '\n' + EXPLANATION_INSTRUCTIONS
CHAT_STYLE += '\n' + CHECKING_INSTRUCTIONS
