"""Import this checkout as the pcb_checker package, whatever the folder is named.

The repository root is the pcb_checker package (it holds __init__.py), so a
plain import only resolves when the checkout folder itself is named
pcb_checker. Importing this helper first registers the real folder under that
package name using only the standard library. When the folder already has the
right name, the normal import wins and nothing changes.
"""
import importlib.util
import sys
from pathlib import Path

try:
    import pcb_checker  # noqa: F401
except ModuleNotFoundError as exc:
    if exc.name != 'pcb_checker':
        raise
    _ROOT = Path(__file__).resolve().parent
    _SPEC = importlib.util.spec_from_file_location(
        'pcb_checker', _ROOT / '__init__.py',
        submodule_search_locations=[str(_ROOT)])
    _MODULE = importlib.util.module_from_spec(_SPEC)
    sys.modules['pcb_checker'] = _MODULE
    _SPEC.loader.exec_module(_MODULE)
