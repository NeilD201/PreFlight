"""Regression coverage for the requirements and omissions audit.

Every omission must fire on a faulty fixture and stay silent on the
corrected fixture. Existing LED/swap detection must keep working.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.checks.requirements import requirements_audit_findings
from pcb_checker.checks.values import value_sanity_findings
from test_value_sanity import comp, project_with


def rules(project):
    return {f["rule"] for f in requirements_audit_findings(project)}


def doc_project(project, groups, docs):
    project.review = {'groups': groups}
    project.datasheets = docs
    return project


class DatasheetQuoteTests(unittest.TestCase):
    def test_retrieved_regulator_requirement_quoted_in_finding(self):
        from pcb_checker.checks.requirements import attach_datasheet_evidence
        project = reg([('C105', 'V3R3')])
        groups = [dict(f) for f in requirements_audit_findings(project)
                  if f['rule'] == 'regulator_output_capacitor_type']
        self.assertEqual(len(groups), 1)
        group = groups[0]
        group['evidence'] = list(group['evidence'])
        group['sources'] = []
        group['components'] = ['U3']
        docs = {'parts': [{'reference': 'U3', 'code': 'C100', 'status': 'ready'}],
                'documents': {'C100': {'mpn': 'LD1117V', 'manufacturer': 'ST',
                                          'product_url': 'http://example.invalid/ld1117',
                                          'pages': [{'page': 7, 'text': 'Output capacitor: a 10uF tantalum capacitor is required for stability across load. Ceramic-only output capacitance with very low ESR may oscillate.'}]}}}
        project = doc_project(project, [group], docs)
        self.assertEqual(attach_datasheet_evidence(project), 1)
        self.assertTrue(any('LD1117V' in e and '10uF tantalum' in e for e in group['evidence']),
                        'the retrieved requirement must be quoted verbatim')
        self.assertTrue(any('LD1117V' in s.get('title', '') for s in group['sources']))
        self.assertEqual(attach_datasheet_evidence(project), 0, 'quotes must not duplicate on re-run')

    def test_quote_prefers_requirement_sentence_over_boilerplate(self):
        from pcb_checker.checks.requirements import _quote_passage, _REGULATOR_QUOTE_KEYS
        doc = {'pages': [
            {'page': 1, 'text': 'Features: low dropout voltage for stability across temperature. Output current up to 800 mA.'},
            {'page': 7, 'text': 'Only a very common 10 uF minimum capacitor is needed for stability. The TAB is connected to the VOUT.'}]}
        page, quote = _quote_passage(doc, _REGULATOR_QUOTE_KEYS)
        self.assertEqual(page, 7)
        self.assertIn('10 uF', quote)

    def test_quote_handles_plain_string_pages(self):
        from pcb_checker.checks.requirements import _quote_passage, _REGULATOR_QUOTE_KEYS
        doc = {'pages': ['Output capacitor: a 10uF tantalum capacitor is required for stability.']}
        page, quote = _quote_passage(doc, _REGULATOR_QUOTE_KEYS)
        self.assertIn('10uF', quote)

    def test_missing_documents_leave_findings_untouched(self):
        from pcb_checker.checks.requirements import attach_datasheet_evidence
        project = reg([('C105', 'V3R3')])
        groups = [dict(f) for f in requirements_audit_findings(project)]
        before = [len(g['evidence']) for g in groups]
        project = doc_project(project, groups, {'parts': [], 'documents': {}})
        self.assertEqual(attach_datasheet_evidence(project), 0)
        self.assertEqual([len(g['evidence']) for g in project.review['groups']], before)


def reg(output_caps=(), input_caps=()):
    parts = [comp("U3", "LD1117V", "SOT-223", "",
                  pins=[("1", "GND", "power_in", "GND"),
                        ("2", "VO", "power_out", "V3R3"),
                        ("4", "VI", "power_in", "V5")])]
    for i, (ref, net) in enumerate(output_caps):
        parts.append(comp(ref, "100uF", "C_0402", "",
                          pads=[("1", net, ""), ("2", "GND", "")]))
    for i, (ref, net) in enumerate(input_caps):
        parts.append(comp(ref, "10uF", "C_0805", "",
                          pads=[("1", net, ""), ("2", "GND", "")]))
    return project_with(*parts)


def usb(pull_cc=True, esd=False, fuse=False):
    parts = [comp("J1", "USB_C", "Conn_USB_C", "",
                  pins=[("A5", "CC1", "passive", "CC1"), ("B5", "CC2", "passive", "CC2"),
                        ("A6", "DP", "passive", "USB_DP"), ("A7", "DN", "passive", "USB_DN"),
                        ("B1", "VBUS", "power_in", "VBUS")])]
    if pull_cc:
        parts.append(comp("R8", "5.1k", "R_0402", "", pads=[("1", "CC1", ""), ("2", "GND", "")]))
        parts.append(comp("R9", "5.1k", "R_0402", "", pads=[("1", "CC2", ""), ("2", "GND", "")]))
    if esd:
        parts.append(comp("D5", "PESD5V0S1BL", "SOD-323", "",
                          pads=[("1", "USB_DP", ""), ("2", "GND", "")]))
    if fuse:
        parts.append(comp("F1", "MF-MSMF050", "SMD", "",
                          pads=[("1", "VBUS", ""), ("2", "VBUS_FUSED", "")]))
    return project_with(*parts)


class RegulatorTests(unittest.TestCase):
    def test_missing_output_cap_flagged(self):
        self.assertIn("regulator_output_capacitor", rules(reg()))

    def test_fitted_output_cap_silent(self):
        self.assertNotIn("regulator_output_capacitor",
                         rules(reg(output_caps=[("C105", "V3R3")])))

    def test_ceramic_only_output_flagged_for_type(self):
        found = {f["rule"] for f in requirements_audit_findings(
            reg(output_caps=[("C105", "V3R3"), ("C106", "V3R3")]))}
        self.assertIn("regulator_output_capacitor_type", found)

    def test_bulk_output_cap_silent(self):
        project = reg(output_caps=[("C105", "V3R3")])
        project.components["C105"].schematic[0].footprint = "Cap_Tantalum_D"
        self.assertNotIn("regulator_output_capacitor_type", rules(project))

    def test_missing_input_cap_flagged(self):
        self.assertIn("regulator_input_capacitor", rules(reg()))

    def test_input_cap_silent(self):
        self.assertNotIn("regulator_input_capacitor",
                         rules(reg(input_caps=[("C107", "V5")])))

    def test_non_regulator_ic_ignored(self):
        project = project_with(comp("U9", "NE555", "SO-8", "",
                                        pins=[("1", "GND", "power_in", "GND"),
                                              ("8", "VCC", "power_in", "VCC")]))
        self.assertEqual(rules(project), set())


def ds_project(project, parts=(), documents=None):
    project.datasheets = {'parts': list(parts), 'documents': dict(documents or {})}
    return project


def u3_board(caps=(), u3_status='ready', u3_code='C100', doc_text=None):
    parts = [comp("U3", "LD1117V", "SOT-223", "",
                  pins=[("1", "GND", "power_in", "GND"),
                        ("2", "VO", "power_out", "V3R3"),
                        ("4", "VI", "power_in", "V5")])]
    for ref, value, footprint in caps:
        parts.append(comp(ref, value, footprint, "",
                          pads=[("1", "V3R3", ""), ("2", "GND", "")]))
    project = project_with(*parts)
    entries = [{'reference': 'U3', 'code': u3_code, 'status': u3_status}]
    for ref, _value, _footprint in caps:
        entries.append({'reference': ref, 'status': 'ready'})
    documents = {}
    if doc_text is not None:
        documents[u3_code] = {'mpn': 'LD1117V', 'manufacturer': 'ST',
                              'pages': [{'page': 7, 'text': doc_text}]}
    return ds_project(project, entries, documents)


class SourcingAnomalyTests(unittest.TestCase):
    def board(self, status='unmatched', value='100uF', footprint='C_0402'):
        project = project_with(comp("C105", value, footprint, "",
                                     pads=[("1", "+5V", ""), ("2", "GND", "")]))
        return ds_project(project, [{'reference': 'C105', 'status': status}])

    def test_complete_specs_without_candidate_flagged(self):
        found = [f for f in requirements_audit_findings(self.board())
                 if f['rule'] == 'mlcc_package_plausibility']
        self.assertEqual(len(found), 1)
        self.assertIn('C105', found[0]['title'])
        self.assertIn('100uF', found[0]['summary'])

    def test_matched_part_silent(self):
        self.assertNotIn('mlcc_package_plausibility', rules(self.board(status='ready')))

    def test_matching_never_ran_silent(self):
        project = project_with(comp("C105", "100uF", "C_0402", "",
                                     pads=[("1", "+5V", ""), ("2", "GND", "")]))
        project.datasheets = {}
        self.assertNotIn('mlcc_package_plausibility', rules(project))

    def test_unparseable_value_silent(self):
        self.assertNotIn('mlcc_package_plausibility', rules(self.board(value='DNP')))


class LedResistorTests(unittest.TestCase):
    def board(self, resistor='10k', rail='Net-GPIO26'):
        return project_with(
            comp("D168", "LED", "LED_0603", "Device:LED",
                 pads=[("1", "Net-DA", ""), ("2", "GND", "")]),
            comp("R12", resistor, "R_0603", "",
                 pads=[("1", "Net-DA", ""), ("2", rail, "")]))

    def test_dim_for_every_color_flagged_with_exact_fix(self):
        found = [f for f in requirements_audit_findings(self.board())
                 if f['rule'] == 'led_current_limiting']
        self.assertEqual(len(found), 1)
        self.assertIn('too low', found[0]['title'])
        self.assertIn('560', found[0]['next_step'])

    def test_sensible_resistor_silent(self):
        self.assertNotIn('led_current_limiting', rules(self.board(resistor='680')))

    def test_overcurrent_flagged(self):
        found = [f for f in requirements_audit_findings(self.board(resistor='47', rail='+5V'))
                 if f['rule'] == 'led_current_limiting']
        self.assertEqual(len(found), 1)
        self.assertIn('excessive', found[0]['title'])

    def test_unknown_supply_silent(self):
        self.assertNotIn('led_current_limiting', rules(self.board(rail='Net-UNKNOWN')))


class RegulatorValueTests(unittest.TestCase):
    STABLE_DOC = ('Only a very common 10 uF minimum capacitor is needed for stability. '
                  'The tab is connected to Vout.')

    def test_effective_value_unproven_when_mlcc_only(self):
        project = u3_board([("C105", "100uF", "C_0402")], doc_text=self.STABLE_DOC)
        found = [f for f in requirements_audit_findings(project)
                 if f['rule'] == 'regulator_output_capacitor_value']
        self.assertEqual(len(found), 1)
        self.assertIn('unproven', found[0]['title'])
        self.assertIn('10', found[0]['summary'])
        type_rules = [f for f in requirements_audit_findings(project)
                      if f['rule'] == 'regulator_output_capacitor_type']
        self.assertEqual(type_rules, [], 'the specific value finding replaces the generic type finding')

    def test_deficit_flagged_for_review(self):
        project = u3_board([("C105", "1uF", "C_0402")], doc_text=self.STABLE_DOC)
        found = [f for f in requirements_audit_findings(project)
                 if f['rule'] == 'regulator_output_capacitor_value']
        self.assertEqual(len(found), 1)
        # 'review' is the only valid grade: omission findings need engineer
        # judgment, and 'action' is not a priority the review assembler knows.
        self.assertEqual(found[0]['priority'], 'review')
        self.assertIn('below the datasheet minimum', found[0]['title'])

    def test_verified_bulk_silent(self):
        project = u3_board([("C105", "22uF", "Cap_Tantalum_D")], doc_text=self.STABLE_DOC)
        found = [f for f in requirements_audit_findings(project)
                 if f['rule'] == 'regulator_output_capacitor_value']
        self.assertEqual(found, [])

    def test_no_document_silent(self):
        project = u3_board([("C105", "100uF", "C_0402")], doc_text=None)
        self.assertNotIn('regulator_output_capacitor_value', rules(project))

    def test_quote_attaches_to_value_finding(self):
        from pcb_checker.checks.requirements import attach_datasheet_evidence
        project = u3_board([("C105", "100uF", "C_0402")], doc_text=self.STABLE_DOC)
        groups = [dict(f) for f in requirements_audit_findings(project)
                  if f['rule'] == 'regulator_output_capacitor_value']
        self.assertEqual(len(groups), 1)
        doc_project(project, groups, project.datasheets)
        self.assertEqual(attach_datasheet_evidence(project), 1)
        self.assertTrue(any('10 uF minimum' in e for e in groups[0]['evidence']))


class UsbProtectionTests(unittest.TestCase):
    def test_missing_esd_flagged(self):
        self.assertIn("usb_data_line_protection", rules(usb()))

    def test_fitted_esd_silent(self):
        self.assertNotIn("usb_data_line_protection", rules(usb(esd=True)))

    def test_missing_fuse_flagged(self):
        self.assertIn("usb_vbus_overcurrent", rules(usb()))

    def test_fitted_fuse_silent(self):
        self.assertNotIn("usb_vbus_overcurrent", rules(usb(fuse=True)))

    def test_dash_pin_names_detected(self):
        project = project_with(comp("J1", "USB_C", "Conn_USB_C", "",
                                        pins=[("A6", "D+", "passive", "D+"),
                                              ("A7", "D-", "passive", "D-"),
                                              ("B1", "VBUS", "power_in", "VBUS")]))
        self.assertIn("usb_data_line_protection", rules(project))

    def test_non_usb_connector_ignored(self):
        project = project_with(comp("J2", "Conn_2x05", "IDC", "",
                                        pins=[("1", "TX", "passive", "TX"),
                                              ("2", "RX", "passive", "RX")]))
        self.assertEqual(rules(project), set())


class RailGapTests(unittest.TestCase):
    def board(self, covered):
        pins = [("1", "VDD1", "power_in", "VDD1"), ("2", "VDD2", "power_in", "VDD2"),
                ("3", "GND", "power_in", "GND")] + [
                (str(i), f"P{i}", "passive", f"N{i}") for i in range(4, 10)]
        parts = [comp("U4", "SOMEIC", "QFN-32", "", pins=pins)]
        for ref, net in covered:
            parts.append(comp(ref, "100nF", "C_0402", "",
                              pads=[("1", net, ""), ("2", "GND", "")]))
        return project_with(*parts)

    def test_partial_gap_names_bare_rail(self):
        findings = [f for f in requirements_audit_findings(self.board([("C1", "VDD1")]))
                    if f["rule"] == "ic_decoupling_rail_gap"]
        self.assertEqual(len(findings), 1)
        self.assertIn("VDD2", findings[0]["summary"])
        self.assertNotIn("VDD1 has no", findings[0]["summary"])

    def test_fully_covered_silent(self):
        self.assertNotIn("ic_decoupling_rail_gap",
                         rules(self.board([("C1", "VDD1"), ("C2", "VDD2")])))

    def test_all_bare_left_to_presence_rule(self):
        # The all-rails-bare case stays owned by ic_decoupling_presence.
        self.assertNotIn("ic_decoupling_rail_gap", rules(self.board([])))


class BaselineRegressionTests(unittest.TestCase):
    def test_led_starved_still_flagged(self):
        project = project_with(
            comp("D1", "LED", "LED_0402", "",
                 pads=[("A", "N1", ""), ("C", "GND", "")]),
            comp("R12", "10k", "R_0402", "",
                 pads=[("1", "V3R3", ""), ("2", "N1", "")]))
        self.assertIn("led_series_resistance",
                      {f["rule"] for f in value_sanity_findings(project)})

    def test_audit_stays_silent_on_minimal_led_board(self):
        project = project_with(
            comp("D1", "LED", "LED_0402", "",
                 pads=[("A", "N1", ""), ("C", "GND", "")]),
            comp("R12", "10k", "R_0402", "",
                 pads=[("1", "V3R3", ""), ("2", "N1", "")]))
        self.assertEqual(rules(project), set())


class ReviewAssemblyContractTests(unittest.TestCase):
    """Every audit priority must be sortable by the review assembler.

    Regression: the omissions audit emitted 'action', which review.ORDER did
    not know, so build_review crashed with KeyError after minutes of KiCad
    work and no report was saved.
    """

    def omission_boards(self):
        return [
            reg(),  # regulator output with no capacitor fitted
            LedResistorTests().board(),  # starved LED chain
            LedResistorTests().board(resistor='47', rail='+5V'),  # overdriven LED
            u3_board([("C105", "1uF", "C_0402")],
                     doc_text=RegulatorValueTests.STABLE_DOC),  # below-minimum bulk
            usb(pull_cc=True),  # missing USB protection parts
        ]

    def test_audit_priorities_are_known_to_review(self):
        from pcb_checker.review import ORDER
        seen = set()
        for project in self.omission_boards():
            for finding in requirements_audit_findings(project):
                seen.add(finding['priority'])
                self.assertIn(finding['priority'], ORDER,
                              f"rule {finding['rule']} emits an unsortable priority")
        self.assertTrue(seen, 'fixtures must trigger omission findings')

    def test_review_sort_accepts_omission_groups(self):
        from pcb_checker.review import ORDER, CATEGORIES
        groups = []
        for project in self.omission_boards():
            for finding in requirements_audit_findings(project):
                groups.append({**finding, 'components': [finding['reference']],
                               'basis': finding.get('basis', 'Sourced part rule')})
        self.assertTrue(groups, 'fixtures must trigger omission findings')
        ordered = sorted(groups, key=lambda g: (ORDER[g['priority']],
                                                 g['basis'] != 'Sourced part rule',
                                                 list(CATEGORIES).index(g['category']),
                                                 g['title']))
        self.assertEqual(len(ordered), len(groups))


class LoadAwareCapacitorTests(unittest.TestCase):
    """C105/C106 scenario: 100uF 0402 on logic rails with a real load IC.

    The finding must name the powered IC (reference, pin, net), compare the
    rail capacitance against the IC datasheet when retrieved, and state
    exactly what is unverified when no document exists.
    """
    DOC = ('Place a 0.1 uF bypass capacitor from VCC to ground, as close as '
           'possible to pin 8. A larger bulk capacitor is recommended where '
           'the supply runs over a long cable.')

    def board(self, caps=(("C105", "100uF", "C_0402", "+5V"),),
              u9_doc=None):
        parts = [comp("U9", "NE555", "SO-8", "",
                      pins=[("1", "GND", "power_in", "GND"),
                            ("8", "VCC", "power_in", "+5V")])]
        for ref, value, footprint, net in caps:
            parts.append(comp(ref, value, footprint, "",
                              pads=[("1", net, ""), ("2", "GND", "")]))
        project = project_with(*parts)
        entries = [{'reference': 'U9', 'code': 'C200', 'status': 'ready'}]
        documents = {}
        if u9_doc is not None:
            documents['C200'] = {'mpn': 'NE555', 'manufacturer': 'TI',
                                 'pages': [{'page': 9, 'text': u9_doc}]}
        return ds_project(project, entries, documents)

    def mlcc(self, project):
        return [f for f in value_sanity_findings(project)
                if f['rule'] == 'mlcc_package_plausibility']

    def attached(self, project):
        from pcb_checker.checks.requirements import attach_datasheet_evidence
        project.review = {'groups': self.mlcc(project)}
        attach_datasheet_evidence(project)
        return project.review['groups']

    def test_finding_names_load_ic_pin_and_net(self):
        found = self.mlcc(self.board())
        self.assertEqual(len(found), 1)
        text = found[0]['summary'] + '|' + '|'.join(found[0]['evidence'])
        self.assertIn('U9', text)
        self.assertIn('pin 8', text)
        self.assertIn('+5V', text)

    def test_sibling_caps_labeled_by_role(self):
        found = self.mlcc(self.board(caps=(("C105", "100uF", "C_0402", "+5V"),
                                            ("C107", "100nF", "C_0603", "+5V"))))
        self.assertEqual(len(found), 1)
        text = '|'.join(found[0]['evidence'])
        self.assertIn('C107', text)
        self.assertIn('100nF', text)

    def test_unverified_names_missing_ic_datasheet(self):
        found = self.mlcc(self.board())
        text = ' '.join(found[0]['steps'])
        self.assertIn('U9', text)
        self.assertIn('unverified', text.lower())

    def test_load_requirement_quoted_with_citation(self):
        groups = self.attached(self.board(u9_doc=self.DOC))
        self.assertEqual(len(groups), 1)
        evidence = ' '.join(groups[0]['evidence'])
        self.assertIn('NE555', evidence)
        self.assertIn('9', evidence)
        self.assertIn('0.1', evidence)
        titles = [s.get('title', '') for s in groups[0]['sources']]
        self.assertTrue(any('NE555' in t for t in titles))

    def test_missing_recommended_value_flagged(self):
        groups = self.attached(self.board(u9_doc=self.DOC))
        text = ' '.join(groups[0]['evidence'] + groups[0]['steps'])
        self.assertIn('0.1', text)
        self.assertIn('local', text.lower())

    def test_identical_loads_collapse_to_one_question(self):
        project = project_with(
            comp("U9", "NE555", "SO-8", "",
                 pins=[("1", "GND", "power_in", "GND"),
                       ("8", "VCC", "power_in", "+5V")]),
            comp("U10", "NE555", "SO-8", "",
                 pins=[("1", "GND", "power_in", "GND"),
                       ("8", "VCC", "power_in", "+5V")]),
            comp("C105", "100uF", "C_0402", "",
                 pads=[("1", "+5V", ""), ("2", "GND", "")]))
        found = [f for f in value_sanity_findings(project)
                 if f['rule'] == 'mlcc_package_plausibility']
        self.assertEqual(len(found), 1)
        load_steps = [s for s in found[0]['steps'] if 'unverified' in s]
        self.assertEqual(len(load_steps), 1)
        self.assertIn('U10', load_steps[0])
        self.assertIn('+1 same parts', load_steps[0])

    def test_covered_value_not_flagged_missing(self):
        groups = self.attached(self.board(
            caps=(("C105", "100uF", "C_0402", "+5V"),
                  ("C107", "100nF", "C_0603", "+5V")), u9_doc=self.DOC))
        text = ' '.join(groups[0]['evidence'] + groups[0]['steps'])
        self.assertNotIn('no 0.1', text.lower())
        self.assertNotIn('missing the recommended', text.lower())


if __name__ == "__main__":
    unittest.main()
