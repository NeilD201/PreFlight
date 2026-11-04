"""Per-action request limits shared by retries, providers and correction passes."""
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


LIMITS = {
    # One complete-investigation tier. Low-usage economy mode is gone:
    # analysis budgets must not starve evidence gathering, challenge loops
    # or repair retries. The euro spending cap is enforced separately.
    'complete': {'review': 12, 'repair': 20, 'chat': 4, 'connection': 2, 'firmware': 8},
}
_current = ContextVar('ai_request_budget', default=None)


@dataclass
class RequestBudget:
    limit: int
    mode: str
    requests: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def check(self):
        with self._lock:
            requests, limit = self.requests, self.limit
        if requests >= limit:
            from .reviewer import AIError
            raise AIError(
                f'The {limit}-request limit for this action was reached. '
                'Completed checks were kept; remaining checks are unresolved. '
                'No further AI requests were sent. Review usage settings before starting another action.',
                diagnostics={'error_kind': 'request_budget', 'retryable': False,
                             'requests': requests, 'request_limit': limit})

    def consume(self, progress=None):
        with self._lock:
            if self.requests >= self.limit:
                limit, requests = self.limit, self.requests
                from .reviewer import AIError
                raise AIError(
                    f'The {limit}-request limit for this action was reached. '
                    'Completed checks were kept; remaining checks are unresolved. '
                    'No further AI requests were sent. Review usage settings before starting another action.',
                    diagnostics={'error_kind': 'request_budget', 'retryable': False,
                                 'requests': requests, 'request_limit': limit})
            number, remaining = self.requests + 1, self.limit - self.requests - 1
            self.requests += 1
        if progress:
            progress(f'Vector request {number} used · {remaining} remaining (limit {self.limit} per action, resets each action) · {self.mode} mode…')

    def summary(self):
        return {'mode': self.mode, 'requests': self.requests, 'request_limit': self.limit}


def current_budget():
    return _current.get()


@contextmanager
def request_budget(mode='complete', action='review'):
    # A nested reviewer cannot reset the allowance of its containing action.
    existing = current_budget()
    if existing is not None:
        yield existing
        return
    # Legacy stored modes ('economy', 'detailed') map to the single tier.
    tier = LIMITS.get(mode, LIMITS['complete'])
    budget = RequestBudget(tier[action], 'complete')
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)
