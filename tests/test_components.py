"""Behavior checks with small synthetic KiCad fixtures; no private designs stored."""

from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.analysis import analyze_project
from pcb_checker.archive import ArchiveError, import_project, read_project_file
from pcb_checker.checks.components import values_match
from pcb_checker.parsers.kicad_pcb import parse_pcb
from pcb_checker.parsers.kicad_schematic import parse_schematic
from pcb_checker.parsers.sexpr import ParseError, parse


def symbol(ref="C1", value="100nF", footprint="Cap:C_0402", unit=1, flags="", extra=""):
    return f'''(symbol (lib_id "Device:C") (unit {unit}) (uuid "{ref}-{unit}")
      (property "Reference" "{ref}") (property "Value" "{value}")
      (property "Footprint" "{footprint}") {flags} {extra})'''


def footprint(ref="C1", value="100nF", name="Cap:C_0402", attr="smd", legacy=False):
    fields = (f'(fp_text reference "{ref}") (fp_text value "{value}")' if legacy else
              f'(property "Reference" "{ref}") (property "Value" "{value}")')
    return f'(footprint "{name}" {fields} (attr {attr}))'


def schematic(*symbols, extra=""):
    return '(kicad_sch (version 20250114) (uuid "root") ' + ' '.join(symbols) + extra + ')'


def board(*footprints):
    return '(kicad_pcb (version 20241229) ' + ' '.join(footprints) + ')'


class ParserTests(unittest.TestCase):
    def test_nested_library_symbols_do_not_become_components(self):
        library = '(lib_symbols (symbol "Device:C" (property "Reference" "C") (symbol "C_1_1")))'
        result = parse_schematic(schematic(symbol(), symbol("#PWR01", "GND", ""), extra=library), "demo.kicad_sch")
        self.assertEqual([r.reference for r in result.components], ["C1"])
        self.assertEqual(result.non_physical_symbols, 1)

    def test_quotes_parentheses_and_source_lines(self):
        text = '(kicad_pcb\n' + footprint(value=r'quoted \"value\" (test) µ \\') + ')'
        record = parse_pcb(text, "test.kicad_pcb").components[0]
        self.assertEqual(record.value, 'quoted "value" (test) µ \\')
        self.assertEqual(record.location, "test.kicad_pcb:2")

    def test_old_pcb_field_format(self):
        record = parse_pcb(board(footprint(legacy=True)), "board.kicad_pcb").components[0]
        self.assertEqual((record.reference, record.value, record.footprint), ("C1", "100nF", "Cap:C_0402"))

    def test_instance_reference_overrides_display_field(self):
        instances = '''(instances
          (project "unrelated" (path "/root" (reference "C99") (unit 1)))
          (project "demo" (path "/root" (reference "C2") (unit 1))))'''
        result = parse_schematic(schematic(symbol("C?", extra=instances)), "demo.kicad_sch")
        self.assertTrue(result.comparison_supported)
        self.assertEqual(result.components[0].reference, "C2")
        self.assertEqual(result.components[0].properties["Reference"], "C?")

    def test_blank_project_instance_is_supported(self):
        instances = '(instances (project "" (path "/root" (reference "C3") (unit 1))))'
        result = parse_schematic(schematic(symbol(extra=instances)), "demo.kicad_sch")
        self.assertTrue(result.comparison_supported)
        self.assertEqual(result.components[0].reference, "C3")

    def test_old_schematic_instance_fields(self):
        extra = '(symbol_instances (path "/C1-1" (reference "C4") (unit 1) (value "1uF") (footprint "Cap:C_0603")))'
        result = parse_schematic(schematic(symbol(), extra=extra), "demo.kicad_sch")
        record = result.components[0]
        self.assertEqual((record.reference, record.value, record.footprint), ("C4", "1uF", "Cap:C_0603"))

    def test_malformed_or_wrong_source_is_rejected(self):
        for text in ('', '(kicad_pcb', '(kicad_pcb))', '(kicad_pcb) junk',
                     '(kicad_pcb) (kicad_pcb)', '(kicad_sch)',
                     '(kicad_pcb (property "unterminated))', '(kicad_pcb ((foo)))'):
            with self.subTest(text=text), self.assertRaises(ParseError):
                parse_pcb(text, "bad.kicad_pcb")

    def test_parser_limits(self):
        with patch('pcb_checker.parsers.sexpr.MAX_DEPTH', 2), self.assertRaises(ParseError):
            parse('(kicad_pcb (a (b)))', 'kicad_pcb')
        with patch('pcb_checker.parsers.sexpr.MAX_TOKENS', 2), self.assertRaises(ParseError):
            parse('(kicad_pcb)', 'kicad_pcb')

    def test_passive_value_equivalence_and_case(self):
        for ref, a, b in [('C1', '100nf', '0.1uF'), ('R1', '4k7', '4700'),
                          ('R1', '4R7', '4.7Ω'), ('L1', '1µH', '1000nH'),
                          ('R1', '10K', '10k'), ('C1', '1e-6F', '1uF')]:
            with self.subTest(a=a, b=b):
                self.assertTrue(values_match(ref, a, b))
        for ref, a, b in [('R1', '1M', '1m'), ('U1', 'Part-A', 'Part-B'),
                          ('C1', '10uF', '10nF'), ('U1', 'Part-A', 'part-a')]:
            with self.subTest(a=a, b=b):
                self.assertFalse(values_match(ref, a, b))


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'project'
        self.folder.mkdir()

    def write_project(self, sch=None, pcb=None):
        if sch is not None:
            (self.folder / 'demo.kicad_sch').write_text(sch)
        if pcb is not None:
            (self.folder / 'demo.kicad_pcb').write_text(pcb)

    def analyze(self):
        return analyze_project(self.folder)

    def test_successful_merge_retains_both_sources(self):
        self.write_project(schematic(symbol()), board(footprint(value="0.1uF")))
        result = self.analyze()
        self.assertEqual(result.stage, 'component_comparison')
        self.assertEqual(result.components['C1'].schematic[0].value, '100nF')
        self.assertEqual(result.components['C1'].pcb[0].value, '0.1uF')
        self.assertEqual([i.code for i in result.issues], ['ASSEMBLY_NOT_PERFORMED','CHECKS_NOT_IMPLEMENTED'])
        # The internal schematic netlister lets pin-level comparison run even
        # without a KiCad CLI export, so the four connectivity stages join
        # the four component stages (previously the count stalled at 4 here).
        # The requirements-and-omissions audit is a distinct recorded stage.
        self.assertEqual(result.checks_performed,
            ['component_reference_and_field_validation', 'schematic_pcb_reference_comparison',
             'value_comparison', 'footprint_name_comparison', 'pin_pad_correspondence',
             'pin_net_assignment_comparison', 'net_split_and_merge_detection',
             'no_connect_comparison', 'requirements_audit'])

    def test_missing_and_mismatched_components_have_evidence(self):
        self.write_project(schematic(symbol(), symbol('C2')),
                           board(footprint(value='10uF', name='Cap:C_0603'), footprint('R1')))
        issues = {i.code: i for i in self.analyze().issues}
        self.assertEqual(issues['VALUE_MISMATCH'].component, 'C1')
        self.assertEqual(issues['FOOTPRINT_MISMATCH'].component, 'C1')
        self.assertEqual(issues['SCHEMATIC_COMPONENT_MISSING_FROM_PCB'].component, 'C2')
        self.assertEqual(issues['PCB_COMPONENT_MISSING_FROM_SCHEMATIC'].component, 'R1')
        self.assertEqual(issues['VALUE_MISMATCH'].evidence, ['demo.kicad_sch:1', 'demo.kicad_pcb:1'])

    def test_board_only_and_schematic_exclusions(self):
        self.write_project(schematic(symbol('X1', flags='(on_board no)'), symbol('R1', flags='(in_bom no)')),
                           board(footprint('H1', attr='board_only exclude_from_bom')))
        issues = self.analyze().issues
        self.assertTrue(any(i.code == 'BOARD_ONLY_COMPONENT' and i.component == 'H1' for i in issues))
        self.assertFalse(any(i.component == 'X1' for i in issues))
        # Exclusion from BOM does not mean exclusion from the PCB.
        self.assertTrue(any(i.code == 'SCHEMATIC_COMPONENT_MISSING_FROM_PCB' and i.component == 'R1' for i in issues))

    def test_dnp_still_requires_a_footprint(self):
        self.write_project(schematic(symbol(flags='(dnp yes)')), board())
        self.assertTrue(any(i.code == 'SCHEMATIC_COMPONENT_MISSING_FROM_PCB' for i in self.analyze().issues))

    def test_duplicate_references_are_not_silently_overwritten(self):
        self.write_project(schematic(symbol(), symbol(value='1uF')),
                           board(footprint(), footprint(value='47uF')))
        result = self.analyze()
        self.assertEqual(len(result.components['C1'].pcb), 2)
        self.assertEqual(len(result.components['C1'].schematic), 2)
        codes = {i.code for i in result.issues}
        self.assertTrue({'DUPLICATE_PCB_REFERENCE', 'DUPLICATE_SCHEMATIC_REFERENCE'} <= codes)
        self.assertNotIn('VALUE_MISMATCH', codes)

    def test_distinct_symbol_units_form_one_component(self):
        self.write_project(schematic(symbol('U1', unit=1), symbol('U1', unit=2)), board(footprint('U1')))
        result = self.analyze()
        self.assertEqual(list(result.components), ['U1'])
        self.assertEqual(len(result.components['U1'].schematic), 2)
        self.assertFalse(any(i.severity in ('error', 'warning') for i in result.issues))

    def test_conflicting_units_and_invalid_references(self):
        self.write_project(schematic(symbol('U1', unit=1), symbol('U1', value='different', unit=2), symbol('C?')),
                           board(footprint('U1')))
        codes = {i.code for i in self.analyze().issues}
        self.assertTrue({'SCHEMATIC_UNIT_CONFLICT', 'INVALID_REFERENCE'} <= codes)
        self.assertNotIn('VALUE_MISMATCH', codes)

    def test_failed_parse_does_not_generate_missing_component_findings(self):
        self.write_project('(kicad_sch', board(footprint()))
        result = self.analyze()
        codes = {i.code for i in result.issues}
        self.assertIn('FILE_PARSE_FAILED', codes)
        self.assertIn('COMPONENT_COMPARISON_NOT_PERFORMED', codes)
        self.assertNotIn('PCB_COMPONENT_MISSING_FROM_SCHEMATIC', codes)

    def test_missing_schematic_does_not_generate_missing_component_findings(self):
        self.write_project(pcb=board(footprint()))
        result = self.analyze()
        self.assertEqual(result.stage, 'component_inventory')
        self.assertFalse(any(i.code == 'PCB_COMPONENT_MISSING_FROM_SCHEMATIC' for i in result.issues))

    def test_hierarchy_is_explicitly_partial(self):
        self.write_project(schematic(symbol(), extra='(sheet (property "Sheetfile" "child.kicad_sch"))'),
                           board(footprint(), footprint('C2')))
        result = self.analyze()
        codes = {i.code for i in result.issues}
        self.assertIn('HIERARCHY_SHEET_UNRESOLVED', codes)
        self.assertIn('COMPONENT_COMPARISON_NOT_PERFORMED', codes)
        self.assertNotIn('PCB_COMPONENT_MISSING_FROM_SCHEMATIC', codes)

    def test_foreign_instance_data_does_not_guess_references(self):
        extra = '(instances (project "other" (path "/root" (reference "C99") (unit 1))))'
        self.write_project(schematic(symbol(extra=extra)), board(footprint()))
        codes = {i.code for i in self.analyze().issues}
        self.assertIn('AMBIGUOUS_SYMBOL_INSTANCE', codes)
        self.assertIn('COMPONENT_COMPARISON_NOT_PERFORMED', codes)

    def test_backups_are_not_merged(self):
        self.write_project(schematic(symbol()), board(footprint()))
        (self.folder / '_autosave-demo.kicad_pcb').write_text(board(footprint('C99')))
        backups = self.folder / 'demo-backups'
        backups.mkdir()
        (backups / 'demo.kicad_sch').write_text(schematic(symbol('C99')))
        result = self.analyze()
        self.assertEqual(list(result.components), ['C1'])
        self.assertEqual(sum(f.status == 'skipped_backup' for f in result.files), 2)

    def test_multiple_boards_require_selection(self):
        self.write_project(schematic(symbol()), board(footprint()))
        (self.folder / 'alternate.kicad_pcb').write_text(board(footprint('C2')))
        result = self.analyze()
        self.assertEqual(result.stage, 'component_inventory')
        self.assertNotIn('pcb', result.selected_files)
        selected = analyze_project(self.folder, pcb='demo.kicad_pcb')
        self.assertEqual(selected.stage, 'component_comparison')
        self.assertEqual(list(selected.components), ['C1'])
        with self.assertRaises(ArchiveError):
            analyze_project(self.folder, pcb='missing.kicad_pcb')

    def test_zip_and_directory_produce_same_components(self):
        self.write_project(schematic(symbol()), board(footprint()))
        zip_path = Path(self.temp.name) / 'demo.zip'
        with zipfile.ZipFile(zip_path, 'w') as archive:
            for path in self.folder.iterdir():
                archive.write(path, path.name)
        zipped = analyze_project(zip_path)
        folder = self.analyze()
        self.assertEqual(asdict(zipped)['components'], asdict(folder)['components'])
        self.assertEqual(zipped.issues, folder.issues)

    def test_different_project_names_require_an_explicit_pair(self):
        self.write_project(schematic(symbol()))
        (self.folder / 'different.kicad_pcb').write_text(board(footprint('C2')))
        result = self.analyze()
        self.assertEqual(result.stage, 'component_inventory')
        self.assertTrue(any(i.code == 'SOURCE_PAIR_NOT_CONFIRMED' for i in result.issues))
        confirmed = analyze_project(self.folder, pcb='different.kicad_pcb', schematic='demo.kicad_sch')
        self.assertEqual(confirmed.stage, 'component_comparison')
        self.assertTrue(any(i.code == 'SCHEMATIC_COMPONENT_MISSING_FROM_PCB' for i in confirmed.issues))

    def test_nested_zip(self):
        zip_path = Path(self.temp.name) / 'nested.zip'
        with zipfile.ZipFile(zip_path, 'w') as archive:
            archive.writestr('nested/demo.kicad_sch', schematic(symbol()))
            archive.writestr('nested/demo.kicad_pcb', board(footprint()))
        result = analyze_project(zip_path)
        self.assertEqual(result.stage, 'component_comparison')
        self.assertEqual(result.components['C1'].pcb[0].source_file, 'nested/demo.kicad_pcb')

    def test_directory_links_and_escaping_reads_are_rejected(self):
        outside = Path(self.temp.name) / 'outside.kicad_sch'
        outside.write_text(schematic(symbol()))
        (self.folder / 'linked.kicad_sch').symlink_to(outside)
        with self.assertRaises(ArchiveError):
            self.analyze()
        with self.assertRaises(ArchiveError):
            read_project_file(self.folder, '../outside.kicad_sch')

    def test_directory_total_size_limit(self):
        self.write_project(schematic(symbol()), board(footprint()))
        with patch('pcb_checker.archive.MAX_TOTAL_BYTES', 1), self.assertRaises(ArchiveError):
            import_project(self.folder)

    def test_cli_prompt_and_json(self):
        self.write_project(schematic(symbol()), board(footprint()))
        launcher = Path(__file__).resolve().parents[1] / 'main.py'
        result = subprocess.run([sys.executable, str(launcher), '--json', '--skip-kicad', '--no-sourcing', '--no-save'],
                                input=str(self.folder) + '\n', cwd=self.temp.name,
                                text=True, capture_output=True, check=True)
        report = json.loads(result.stdout)
        self.assertEqual(report['stage'], 'component_comparison')
        self.assertIn('C1', report['components'])
        self.assertIn('Unzipped project folder name', result.stderr)

    def test_prompt_uses_existing_default_when_enter_is_pressed(self):
        from pcb_checker import __main__ as cli
        with tempfile.TemporaryDirectory() as td:
            default = Path(td) / 'smartwatch'
            default.mkdir()
            with patch.dict('os.environ', {'PCB_CHECKER_PROJECT': str(default)}, clear=True), \
                 patch('builtins.input', return_value=''):
                self.assertEqual(cli.prompt_project_folder(), default)


if __name__ == '__main__':
    unittest.main()
