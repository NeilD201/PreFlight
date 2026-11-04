"""Revision-bound engineering estimates. These are models, not product qualification."""
import hashlib
import json
import math

THERMAL_SOURCE = 'https://www.ti.com/lit/an/spra953d/spra953d.pdf'
ESD_SOURCE = 'https://www.ti.com/lit/an/slva680a/slva680a.pdf'


def targets(data, rails):
    from .verification import field
    rows = []
    context = hashlib.sha256(json.dumps(data.get('design_context', {}), sort_keys=True).encode()).hexdigest()

    def add(kind, key, label, description, fields, **facts):
        rows.append(dict(kind=kind, key=kind+':'+key, label=label, section=kind,
                         description=description, fields=fields, context_signature=context, model_version=1, **facts))

    for ref, comp in sorted(data.get('components', {}).items()):
        records = comp.get('schematic') or comp.get('pcb') or []
        if not records or any(r.get('dnp') or r.get('on_board') is False for r in records):
            continue
        if ref.startswith(('U', 'Q')):
            add('thermal', ref, ref+' · '+records[0].get('value', ''),
                'Estimate junction temperature for this part at steady load. Use effective junction-to-ambient thermal resistance from a model or measurement of your board and enclosure. A datasheet test-board value is not proof of your board’s cooling.',
                [field('ambient', 'Hottest surrounding air', '°C', minimum=-273.15),
                 field('loss', 'Worst-case power dissipated in this part', 'W'),
                 field('theta', 'Effective junction-to-ambient thermal resistance for this assembly', '°C/W', positive=True),
                 field('maximum', 'Allowed junction temperature for operation', '°C', minimum=-273.15)], reference=ref)
        if ref.startswith(('J', 'P')):
            nets = sorted({p['net'] for p in comp.get('schematic_pins', []) if p.get('net') and not p.get('no_connect')})
            for net in nets:
                if net.upper() in ('GND','AGND','DGND','VSS'):continue
                add('esd_margin', ref+':'+net, ref+' · '+net,
                    'Screen a proposed protection path for this connector net. Enter worst-case positive-pulse values for the same waveform, temperature and protected pin. This does not identify a fitted TVS, validate its pulse survival, or prove system ESD immunity. Negative pulses need a separate analysis.',
                    [field('normal', 'Highest normal signal voltage', 'V'),
                     field('standoff', 'Protection device working standoff voltage', 'V', positive=True),
                     field('clamp', 'Worst-case clamp voltage at the stated pulse current', 'V', positive=True),
                     field('inductance', 'Protection path parasitic inductance', 'nH'),
                     field('slew', 'Pulse current rise rate', 'A/ns', positive=True),
                     field('maximum', 'Protected pin’s permitted voltage for this pulse duration', 'V', positive=True)], reference=ref, net=net)
    for rail in rails:
        add('transient', rail['label'], rail['label'],
            'Estimate supply drop while local capacitors cover a sudden current deficit. Use effective capacitance after DC bias, tolerance and temperature. Enter current the regulator cannot supply during the response interval, plus the ESR/inductance of the actual loop. This simplified model excludes control-loop dynamics.',
            [field('current', 'Extra current the capacitors must supply', 'A', positive=True),
             field('duration', 'Time before the supply catches up', 'µs', positive=True),
             field('capacitance', 'Effective local capacitance', 'µF', positive=True),
             field('esr', 'Capacitor-bank series resistance', 'mΩ'),
             field('inductance', 'Effective loop and capacitor inductance', 'nH'),
             field('rise', 'Load-current rise time', 'ns', positive=True),
             field('maximum', 'Allowed supply voltage drop', 'V', positive=True)], net=rail['label'])
    for net in data.get('signal_paths', {}).get('board_measurements', {}).get('nets', []):
        trace = net.get('dc_trace', {})
        if trace.get('status') != 'estimated' or not isinstance(trace.get('ohms'), (int,float)) or trace['ohms'] <= 0:continue
        add('trace_load', net['net'], net['net']+' · '+trace['layer'],
            f'Saved single-chain trace resistance: {trace["ohms"]:g} Ω at 25 °C. Calculates voltage drop and copper loss between the recorded trace endpoints. Pads, vias, pours, contacts and the return path are excluded; this is not an ampacity or temperature rating.',
            [field('current', 'Maximum current in this trace', 'A'),
             field('resistance_factor', 'Worst-case resistance multiplier (at least 1)', '×', minimum=1),
             field('maximum_drop', 'Allowed voltage drop in this trace', 'V', positive=True),
             field('maximum_loss', 'Allowed power loss in this trace', 'W', positive=True)], resistance=trace['ohms'], endpoints=trace.get('endpoints_mm', []), net=net['net'])
    return rows


def validate(target, values):
    if target['kind'] == 'thermal' and values['maximum'] <= values['ambient']:
        raise ValueError('The allowed junction temperature must exceed the surrounding air temperature.')
    if target['kind'] == 'esd_margin' and values['clamp'] < values['standoff']:
        raise ValueError('Clamp voltage must not be below the protection device’s working standoff voltage.')


def calculate(target, v):
    kind = target['kind']
    if kind == 'thermal':
        temperature = v['ambient'] + v['loss']*v['theta']
        margin = v['maximum']-temperature
        detail = f'Estimated junction temperature {temperature:.1f} °C; operating limit {v["maximum"]:g} °C; margin {margin:.1f} °C.'
        metrics = {'junction_c':temperature,'margin_c':margin}
        evidence = ['Tj = ambient + dissipated power × effective thermal resistance. Steady-state estimate only.',
                    'The thermal resistance must represent the actual assembly, airflow and neighboring heat sources. No transient thermal or enclosure simulation has run.',THERMAL_SOURCE]
        ok = margin >= 0
    elif kind == 'transient':
        capacitive = v['current']*v['duration']/v['capacitance']
        resistive = v['current']*v['esr']/1000
        inductive = v['inductance']*v['current']/v['rise']
        drop = capacitive+resistive+inductive
        detail = f'Conservative summed drop estimate {drop:.3f} V (capacitor discharge {capacitive:.3f}, ESR {resistive:.3f}, inductance {inductive:.3f}); allowed {v["maximum"]:g} V.'
        metrics = {'drop_v':drop,'capacitive_v':capacitive,'resistive_v':resistive,'inductive_v':inductive}
        evidence = ['ΔV = ΔI × Δt / C + ΔI × ESR + L × ΔI / rise time.',
                    'Assumes a rectangular current deficit and linear current rise. Terms are summed conservatively; their peaks need not coincide. Excludes source impedance dynamics, ringing, startup sequencing and recovery overshoot.']
        ok = drop <= v['maximum']
    elif kind == 'esd_margin':
        overshoot = v['inductance']*v['slew']; stress = v['clamp']+overshoot
        detail = f'Positive-pulse stress estimate {stress:.2f} V, including {overshoot:.2f} V inductive overshoot; entered pin limit {v["maximum"]:g} V. Normal signal {v["normal"]:g} V; protection standoff {v["standoff"]:g} V.'
        metrics = {'stress_v':stress,'inductive_v':overshoot,'margin_v':v['maximum']-stress}
        evidence = ['Protected-node voltage estimate = worst-case clamp voltage + parasitic inductance × pulse current rise rate.',
                    'Standoff must cover the normal signal voltage. Clamp and pin limits must apply to the same pulse. No negative-pulse, TVS energy, dynamic turn-on, signal-integrity or immunity test is included.', ESD_SOURCE]
        ok = stress <= v['maximum'] and v['normal'] <= v['standoff']
    elif kind == 'trace_load':
        resistance = target['resistance']*v['resistance_factor']; drop = v['current']*resistance; loss = v['current']**2*resistance
        detail = f'Trace drop {drop:.4f} V (limit {v["maximum_drop"]:g}); copper loss {loss:.4f} W (limit {v["maximum_loss"]:g}).'
        metrics = {'resistance_ohm':resistance,'drop_v':drop,'loss_w':loss}
        evidence = ['V = I × R; dissipated power = I² × R. Uses the measured straight-chain resistance and entered worst-case multiplier.',
                    'This is a trace-endpoint calculation, not complete pin-to-pin or return-loop resistance. Copper heating and current capacity require a thermal model.']
        ok = drop <= v['maximum_drop'] and loss <= v['maximum_loss']
    else:
        raise ValueError('Unknown engineering calculation.')
    if not all(math.isfinite(n) for n in metrics.values()):raise ValueError('These inputs produce an out-of-range calculation.')
    return dict(status='checked' if ok else 'attention', detail=detail, metrics=metrics, evidence=evidence,
                basis='Calculation within entered assumptions; not physical verification')
