"""Versioned labeled cases for the deterministic pipeline. No provider calls.

Each case builds a synthetic packet, optionally pre-seeds ledger state, and
declares which finding modes must appear (present) and which must not
(forbidden). Fix cases declare the expected Gate B outcome instead.
"""
EVAL_VERSION = 1


def part(ref, value='', pins=(), dnp=False):
    return {'reference': ref, 'value': value, 'footprint': '', 'mpn': '', 'dnp': dnp,
            'pins': [{'number': n, 'name': name, 'net': net} for n, name, net in pins]}


def packet_for(components, context=None, coverage=()):
    packet = {'components': list(components), 'datasheet_coverage': list(coverage),
              'review_groups': [], 'nets': []}
    if context is not None:
        packet['design_context'] = context
    return packet


def _seed(statement, scope):
    def setup(ledger):
        from pcb_checker.ai import ledger as ledger_mod
        ledger_mod.add_claim(ledger, origin='seed', statement=statement,
                             scope={k: list(scope.get(k, ())) for k in ('components', 'nets', 'pins')})
    return setup


def _seed_fix(scope, fix):
    def setup(ledger):
        from pcb_checker.ai import ledger as ledger_mod
        cid = ledger_mod.add_claim(ledger, origin='seed', statement='[PWR-001] seeded.',
                                   scope={k: list(scope.get(k, ())) for k in ('components', 'nets', 'pins')})
        ledger_mod.set_status(ledger, cid, 'under_investigation', reason='seed', actor='seed')
        ledger_mod.set_status(ledger, cid, 'supported', reason='seed', actor='seed')
        next(c for c in ledger['claims'] if c['id'] == cid)['proposed_fix'] = dict(fix)
    return setup


CASES = [
    {'id': 'clean_pullup', 'category': 'interfaces',
     'blurb': 'I2C bus with a pull-up: timing stays open, missing-pull-up must not fire.',
     'packet': packet_for([part('U1', 'SENSOR', [('2', 'SDA', 'BUS')]),
                           part('R1', '4.7k', [('1', '~', 'BUS'), ('2', '~', 'VCC')])]),
     'present': {'IF-002'}, 'forbidden': {'IF-001'}},
    {'id': 'missing_pullup', 'category': 'interfaces',
     'blurb': 'SDA with no resistor: applicable missing-pull-up finding.',
     'packet': packet_for([part('U1', 'SENSOR', [('2', 'SDA', 'BUS')])]),
     'present': {'IF-001'}, 'forbidden': {'IF-002'}},
    {'id': 'lonely_net', 'category': 'structure',
     'blurb': 'Single-pin net reads as an open.',
     'packet': packet_for([part('U1', 'MCU', [('1', 'OUT', 'SOLO')])]),
     'present': {'DSG-001'}, 'forbidden': set()},
    {'id': 'floating_pin', 'category': 'protection',
     'blurb': 'Netless pin must be confirmed intentional.',
     'packet': packet_for([part('U1', 'MCU', [('1', 'IN', '')])]),
     'present': {'PRT-002'}, 'forbidden': set()},
    {'id': 'unmatched_part', 'category': 'identity',
     'blurb': 'IC without MPN or document cannot be specified.',
     'packet': packet_for([part('U9', 'QFN-32', [('1', 'VDD', 'VCC')])]),
     'present': {'MFG-001'}, 'forbidden': set()},
    {'id': 'decoupled_ok', 'category': 'power',
     'blurb': 'Capacitor on the supply net silences decoupling.',
     'packet': packet_for([part('U1', 'MCU', [('1', 'VDD', 'VCC')]),
                           part('C1', '100n', [('1', '~', 'VCC'), ('2', '~', 'GND')])]),
     'present': set(), 'forbidden': {'PWR-001'}},
    {'id': 'unpaired_usb', 'category': 'interfaces',
     'blurb': 'USB_DP without its mate is flagged.',
     'packet': packet_for([part('U1', 'MCU', [('1', 'USB_DP', 'D1')])]),
     'present': {'DSG-003'}, 'forbidden': set()},
    {'id': 'crystal_open', 'category': 'clock',
     'blurb': 'Crystal pins without load capacitors.',
     'packet': packet_for([part('U1', 'MCU', [('1', 'XI', 'X1'), ('2', 'XO', 'X2')])]),
     'present': {'AN-002'}, 'forbidden': set()},
    {'id': 'reset_open', 'category': 'clock',
     'blurb': 'Reset net without a pull resistor.',
     'packet': packet_for([part('U1', 'MCU', [('7', 'NRST', 'RST')])]),
     'present': {'CLK-001'}, 'forbidden': set()},
    {'id': 'dnp_live', 'category': 'assembly',
     'blurb': 'DNP part on a live connector net needs intent confirmation.',
     'packet': packet_for([part('J1', 'CONN', [('1', 'S', 'SIG')]),
                           part('C9', '100n', [('1', '~', 'SIG')], dnp=True)]),
     'present': {'SCN-006'}, 'forbidden': set()},
    {'id': 'connector_open', 'category': 'protection',
     'blurb': 'Bare connector net draws guard and misuse findings.',
     'packet': packet_for([part('J1', 'CONN', [('1', 'S', 'SIG')])]),
     'present': {'PRT-001', 'SCN-004'}, 'forbidden': set()},
    {'id': 'multi_supply', 'category': 'startup',
     'blurb': 'Two supply domains question sequencing.',
     'packet': packet_for([part('U1', 'MCU', [('1', 'VDD', 'VCC')]),
                           part('U2', 'SENSOR', [('1', 'VDD', 'VBUS')])]),
     'present': {'SCN-001'}, 'forbidden': set()},
    {'id': 'phantom_claim', 'category': 'robustness',
     'blurb': 'A stale missing-pull-up claim on a pulled-up bus is contradicted.',
     'packet': packet_for([part('U1', 'SENSOR', [('2', 'SDA', 'BUS')]),
                           part('R1', '4.7k', [('1', '~', 'BUS'), ('2', '~', 'VCC')])]),
     'setup': _seed('[IF-001] I2C pull-ups: I2C net BUS has no visible pull-up resistor.',
                    {'components': ['U1'], 'nets': ['BUS'], 'pins': ['U1.2']}),
     'present': {'IF-001'}, 'forbidden': set(), 'status_of': ('IF-001', 'contradicted')},
    {'id': 'stale_claim', 'category': 'robustness',
     'blurb': 'A claim about a nonexistent part is not applicable, never supported.',
     'packet': packet_for([part('U1', 'MCU', [('1', 'VDD', 'VCC')])]),
     'setup': _seed('[PWR-001] Supply decoupling: U9 needs review.',
                    {'components': ['U9'], 'nets': [], 'pins': []}),
     'present': {'PWR-001'}, 'forbidden': set(), 'status_of': ('PWR-001', 'not_applicable')},
    {'id': 'empty_board', 'category': 'boundary',
     'blurb': 'No components yields no claims and no crash.',
     'packet': packet_for([]),
     'present': set(), 'forbidden': set(), 'claims_total': 0},
    {'id': 'fix_reject', 'category': 'fix',
     'blurb': 'A fix naming a missing pin is rejected.',
     'packet': packet_for([part('U1', 'MCU', [('1', 'VDD', 'VCC')])]),
     'setup': _seed_fix({'components': ['U1'], 'nets': [], 'pins': []},
                        {'action': 'Move wire', 'reference': 'U1', 'pin': '9', 'net': 'VCC',
                         'value': '', 'connection': 'U1.9 to VCC.', 'verify_steps': []}),
     'fix_expected': 'rejected'},
    {'id': 'fix_ok', 'category': 'fix',
     'blurb': 'A complete fix on a lone claim passes structure with value caveats.',
     'packet': packet_for([part('U1', 'MCU', [('1', 'VDD', 'VCC')])]),
     'setup': _seed_fix({'components': ['U1'], 'nets': [], 'pins': []},
                        {'action': 'Fit cap', 'reference': 'U1', 'pin': '1', 'net': 'VCC',
                         'value': '100nF', 'connection': 'Across supply.', 'verify_steps': ['Measure.']}),
     'fix_expected': 'structure_ok'},
]
