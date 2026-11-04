"""Regression coverage for bounded project imports and archive error reporting."""

from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.archive import ArchiveError, import_project


class ArchiveImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'project.zip'

    def write_zip(self, files):
        with zipfile.ZipFile(self.path, 'w') as archive:
            for name, content in files:
                archive.writestr(name, content)

    def test_discovery_api_still_inventories_without_parsing(self):
        self.write_zip([
            ('board/demo.kicad_sch', '(kicad_sch)'), ('board/demo.kicad_pcb', '(kicad_pcb)'),
            ('bom.csv', 'Reference,Value,Footprint\n"C1, C2",100n,0402\n'),
            ('cpl.csv', 'Ref;PosX;PosY;Rot;Side\nC1;1;2;0;top\n'),
            ('unknown.csv', 'Thing,Count\na,1\n'), ('fab/top.gtl', 'gerber'),
            ('fab/holes.drl', 'drill'), ('__MACOSX/._demo', 'metadata'),
            ('fabrication-toolkit-options.json', '{}'),
        ])
        project = import_project(self.path)
        self.assertEqual({f.kind for f in project.files},
                         {'schematic', 'pcb', 'bom_candidate', 'cpl_candidate', 'csv_unknown',
                          'gerber', 'drill', 'fabrication_settings'})
        self.assertEqual(project.stage, 'file_discovery_only')
        self.assertEqual([i.code for i in project.issues], ['VERIFICATION_NOT_PERFORMED'])

    def test_unsafe_paths(self):
        for name in ('../escape', '/absolute', 'a/../../escape', 'C:/escape', 'a\\b'):
            with self.subTest(name=name):
                self.write_zip([(name, 'bad')])
                with self.assertRaises(ArchiveError):
                    import_project(self.path)
        self.assertFalse((Path(self.temp.name) / 'escape').exists())

    def test_symlink(self):
        entry = zipfile.ZipInfo('link')
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.write_zip([(entry, '../target')])
        with self.assertRaises(ArchiveError):
            import_project(self.path)

    def test_duplicate_paths(self):
        self.write_zip([('board.kicad_pcb', 'a'), ('BOARD.kicad_pcb', 'b')])
        with self.assertRaises(ArchiveError):
            import_project(self.path)

    def test_resource_limits(self):
        self.write_zip([('a', '12345'), ('b', '12345')])
        for limit, value in (('MAX_ARCHIVE_BYTES', 1), ('MAX_FILE_BYTES', 4),
                             ('MAX_TOTAL_BYTES', 9), ('MAX_ENTRIES', 1)):
            with self.subTest(limit=limit), patch(f'pcb_checker.archive.{limit}', value):
                with self.assertRaises(ArchiveError):
                    import_project(self.path)

    def test_corrupt_and_missing_zip(self):
        self.path.write_bytes(b'not a zip')
        with self.assertRaises(ArchiveError):
            import_project(self.path)
        with self.assertRaises(ArchiveError):
            import_project(self.path.with_name('missing.zip'))

    def test_bad_crc(self):
        self.write_zip([('data.csv', 'unique payload')])
        self.path.write_bytes(self.path.read_bytes().replace(b'unique payload', b'broken payload'))
        with self.assertRaises(ArchiveError):
            import_project(self.path)


if __name__ == '__main__':
    unittest.main()
