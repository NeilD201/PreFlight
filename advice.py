"""Turn available evidence into a conclusion, a useful action, or one missing fact."""
from copy import deepcopy
from .checking_policy import separate_analysis


def apply_advice(plan, data):
    plan = deepcopy(plan)
    groups = {g['id']: g for g in data.get('review', {}).get('groups', [])}
    for action in plan.get('actions', []):
        group = groups.get(action['group_id'], {})
        checks = action.get('pin_checks', [])
        if checks:
            changing = [p for p in checks if p['decision'] == 'change']
            missing = [p for p in checks if p['decision'] == 'needs_context']
            kept = [p for p in checks if p['decision'] == 'keep']
            action['advice_title'] = ('A pin connection needs changing' if changing else
                                      'One operating detail is missing' if len(missing) == 1 else
                                      'Confirm the operating mode' if missing else
                                      'These pin connections match the supplied requirements')
            action['advice_summary'] = action['reason']
            action['checked_connections'] = [f"{p['reference']} pin {p['pin']} → {p['net']}: {p['explanation']}" for p in checks]
            if missing and not changing:
                questions = list(dict.fromkeys(q for p in missing for q in p.get('missing_context', [])))
                action['steps'] = questions
            if kept and not changing and not missing:
                action['steps'] = []
        elif action.get('manufacturer_check'):
            action['advice_title'] = action.get('title', group.get('title', 'Manufacturer check'))
            action['advice_summary'] = action['summary']
        elif action.get('basis') == 'AI guidance from supplied evidence':
            action['advice_summary'] = action['reason']
            action['advice_title'] = action.get('title', group.get('title'))
        else:
            action['advice_summary'] = group.get('summary', '')
        # RF measurements establish a traced path, not whether an unmodeled antenna works.
        has_assessment = (action.get('basis') == 'AI guidance from supplied evidence' and
                          action.get('disposition') == 'investigate')
        if (group.get('signal_path_id') or '').startswith('rf-'):
            path = next((p for p in data.get('signal_paths', {}).get('paths', []) if p['id'] == group['signal_path_id']), {})
            anchor, series = path.get('anchor'), path.get('series_component')
            nets = path.get('nets', [])
            if anchor and series and len(nets) >= 2 and not path.get('terminal'):
                end = nets[-1]['net']
                members = []
                for ref, component in data.get('components', {}).items():
                    pins = [p['number'] for p in component.get('schematic_pins', []) if p.get('net') == end]
                    if pins:
                        records = component.get('schematic') or component.get('pcb') or [{}]
                        members.append(f"{ref} ({records[0].get('value', '')}), pin {', '.join(pins)}")
                action['advice_title'] = f'{anchor}: no antenna connection identified'
                action['advice_summary'] = f'The saved radio connection runs from {anchor} through {series} to {end}. That final connection contains ' + '; '.join(members) + '. No antenna or antenna connector was identified there.'
                action['reason'] = action['advice_summary'] + ' The files alone do not establish whether an antenna exists outside this modeled connection. RF performance has not been calculated.'
                action['disposition']='investigate'
                action['reference_only']=False
                action['consequence']={'text':'If this radio is used without a connected antenna, wireless range and reliability can be severely reduced. The saved RF path has no identified antenna endpoint; performance has not been tested.'}
                if not has_assessment:
                    action['steps'] = ['Use Fix this issue to prepare an antenna connection proposal. Review its part, placement, matching and routing before approving a file edit.']
        # A changed library copy supplies no evidence that replacing it repairs hardware.
        codes = {data['issues'][int(fid[1:])-1]['code'] for fid in group.get('issue_ids', [])
                 if fid.startswith('F') and fid[1:].isdigit() and 0 < int(fid[1:]) <= len(data.get('issues', []))}
        if codes and codes <= {'KICAD_DRC_LIB_FOOTPRINT_MISMATCH', 'KICAD_ERC_LIB_SYMBOL_MISMATCH'}:
            action['advice_title'] = 'Library differences · no repair established'
            action['advice_summary'] = 'The parts saved in this design differ from the installed library. This check has not established which pin numbers, pad shapes or electrical functions changed.'
            action['steps'] = []
            action['reason'] = 'Replacing a customized part with the library copy could change the design. A library mismatch alone is not a reason to replace it.'
        if codes == {'KICAD_ERC_POWER_PIN_NOT_DRIVEN'} and not checks:
            action['advice_title'] = 'ERC cannot identify a declared power source'
            action['advice_summary'] = 'KiCad found a power-input symbol without a declared power-output driver. This warning does not establish whether a real source supplies that connection.'
            action['reason'] = 'A PWR_FLAG only changes ERC’s description of the source. It does not add a supply or repair wiring. No flag or wire edit is proposed without identifying the actual source.'
            action['steps'] = []
        if group.get('category') in ('coverage', 'expected'):
            action['steps'] = []
        separate_analysis(action)
    return plan
