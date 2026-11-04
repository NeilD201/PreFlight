"""Shared observed-fact predicates over packet net names and part identity.

Pure functions with no ledger access, so every discovery stream and review
stage classifies rails, grounds, unconnected pads and heating parts the
same way. Net-name classification is heuristic: unlisted rails are missed
rather than guessed, and every consumer records that assumption.
"""
import re

VERSION = 1

_SUPPLY_EXACT = frozenset({
    'VCC', 'VDD', 'VBAT', 'VBUS', 'VIN', 'VOUT', 'VDDIO', 'VDDA', 'VREF', 'VPP',
    '3V3', '33V', '5V', '5V0', '1V8', '18V', '12V', '9V', 'BAT', 'BATT',
})
_SUPPLY_PREFIX = ('VCC', 'VDD', 'VBAT', 'VBUS', 'VIN', 'VOUT', 'VDDIO', 'VDDA',
                  '3V3', '33V', '5V', '1V8', '18V', 'BAT')

_GROUND_EXACT = frozenset({'GND', 'GNDA', 'GNDD', 'AGND', 'DGND', 'PGND', 'VSS'})

_BATTERY_NEGATIVE = frozenset({'-BAT', '-BATT', 'BAT-', 'BATT-', 'B-'})

_REGULATOR_WORDS = re.compile(r'\b(LDO|REGULATOR|BUCK|BOOST|DCDC|DC-DC)\b')
_DISSIPATOR_WORDS = re.compile(r'\b(LDO|REGULATOR|BUCK|BOOST|DCDC|DC-DC|DRIVER|MOTOR|MOSFET|PA\b|RF_AMP)\b')
_DISSIPATOR_STEMS = ('AMS1117', 'LM317', 'AP2112', 'MCP1700', 'MCP73831', 'MCP73871', 'TP4056', 'TC4056')
_SMALL_LED_PACKAGES = ('0201', '0402', '0603', '1005', '1608', '2012')


def _rail(name):
    return str(name or '').upper().strip().lstrip('+-').replace('.', '')


def is_supply_net(net):
    """Heuristic supply-rail test; dotted KiCad rails (+3.3V) included.

    Battery-negative nets (-Bat) are returns, not supplies, so a resistor
    to -Bat is never read as a pull-up. Negative voltage rails (-5V) stay
    supplies.
    """
    if str(net or '').upper().strip() in _BATTERY_NEGATIVE:
        return False
    name = _rail(net)
    return name in _SUPPLY_EXACT or name.startswith(_SUPPLY_PREFIX)


def is_ground_net(net):
    """Heuristic ground test; battery-negative nets are returns."""
    if str(net or '').upper().strip() in _BATTERY_NEGATIVE:
        return True
    name = _rail(net)
    return name in _GROUND_EXACT or name.startswith('GND')


def is_unconnected_net(net):
    """KiCad netlist names single unconnected pads `unconnected-(REF-PIN-...)`."""
    return str(net or '').startswith('unconnected-(')


def looks_like_regulator(value='', footprint='', mpn=''):
    """Heuristic regulator/charger test; part-number stems have no word boundary."""
    text = f'{value} {footprint} {mpn}'.upper()
    return bool(_REGULATOR_WORDS.search(text) or any(stem in text for stem in _DISSIPATOR_STEMS))


def power_dissipators(components):
    """Refs that may convert meaningful power, from value/footprint/MPN.

    Small-signal indicator LEDs (0201/0402/0603) cannot dissipate significant
    power and stay out; linear-regulator and charger part-number stems match
    without word boundaries so AP2112K and MCP73831-2 variants are found.
    """
    hits = []
    for ref, comp in sorted(components.items()):
        if comp.get('dnp'):
            continue
        text = f"{comp.get('value', '')} {comp.get('footprint', '')} {comp.get('mpn', '')}".upper()
        power_led = re.search(r'\bLED\b', text) and not any(
            code in str(comp.get('footprint', '')).upper() for code in _SMALL_LED_PACKAGES)
        if _DISSIPATOR_WORDS.search(text) or any(stem in text for stem in _DISSIPATOR_STEMS) or power_led:
            hits.append(ref)
    return hits
