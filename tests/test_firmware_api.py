import shutil
import unittest
import sys
from pathlib import Path
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.webapp import AppState, AppError
from pcb_checker.analysis import analyze_project
from test_components import schematic, symbol, board, footprint


class FirmwareApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'project'
        self.source.mkdir()
        (self.source / 'test.kicad_sch').write_text(schematic(symbol(value='10nF')))
        (self.source / 'test.kicad_pcb').write_text(board(footprint(value='100nF')))
        self.before = {p: (self.source / p).read_bytes()
                       for p in ('test.kicad_sch', 'test.kicad_pcb')}
        self.project = analyze_project(self.source)
        self.reports = Path(self.temp.name) / 'reports'
        self.state = AppState(str(self.source), self.reports)
        self.addCleanup(self.state.close)
        self.state.project = self.project
        self.state.report_id = 'review'
        self.state.store.preference('input:review', {'path': str(self.source)})

    def prepare(self):
        record = self.state.prepare_firmware({'report_id': 'review'})
        # Discovery is deterministic and may finish before this snapshot.
        self.assertIn(record['status'], ('preparing', 'validated'))
        self.state.firmware_worker.join(timeout=120)
        return self.state.firmware(record['id'])

    def test_prepare_validates_and_discovers_hardware(self):
        with self.assertRaises(AppError):
            self.state.prepare_firmware({'report_id': 'other'})
        record = self.prepare()
        self.assertEqual(record['status'], 'validated')
        spec = record['spec']
        # Minimal fixture declares no MCU: discovery must stay honest.
        self.assertEqual(spec['mcu']['status'], 'unidentified')
        self.assertTrue(any(q['resolves'] == 'mcu' for q in spec['open_questions']))
        for name, data in self.before.items():
            self.assertEqual((self.source / name).read_bytes(), data)

    def test_list_builds_scoped_to_report(self):
        self.assertEqual(self.state.list_firmware_builds('review'), {'builds': []})
        first = self.prepare()
        second = self.prepare()
        listed = self.state.list_firmware_builds('review')
        self.assertEqual([b['id'] for b in listed['builds']], [second['id'], first['id']])
        with self.assertRaises(AppError):
            self.state.list_firmware_builds('other')

    def test_plan_requires_validated_identified_discovery(self):
        with self.assertRaises(AppError):
            self.state.plan_firmware({'job_id': 'nope'})
        record = self.prepare()
        # Minimal fixture declares no MCU: planning stays blocked, honestly.
        with self.assertRaises(AppError):
            self.state.plan_firmware({'job_id': record['id']})
        stored = self.state.firmware_jobs[record['id']]
        stored['spec']['mcu'] = {"status": "identified", "reference": "U1",
                                 "family": "nRF52", "declared_value": "nRF52840",
                                 "declared_mpn": "", "package": "",
                                 "confidence": "medium", "evidence": []}
        planned = self.state.plan_firmware({'job_id': record['id']})
        self.assertEqual(planned['plan']['framework'], 'Bare-metal C (ARM Cortex-M)')
        self.assertTrue(planned['plan']['toolchain']['available'])
        # The plan persists with the record.
        second = AppState(str(self.source), self.reports)
        try:
            self.assertEqual(second.firmware(record['id'])['plan']['framework'],
                             'Bare-metal C (ARM Cortex-M)')
        finally:
            second.close()

    def test_answer_saves_and_updates_discovery(self):
        record = self.prepare()
        self.assertTrue(record.get("questions"))
        purpose = next(q["need"] for q in record["questions"]
                       if q["resolves"] == "purpose" and q["status"] == "user")
        answered = self.state.answer_firmware(
            {"job_id": record["id"], "need": purpose, "answer": "Log temperature."})
        self.assertEqual(answered["spec"]["stated_purpose"], "Log temperature.")
        self.assertTrue(any(a["need"] == purpose and a["answer"] == "Log temperature."
                            for a in answered["answers"]))
        # The answered need no longer routes; the answer record is the proof.
        self.assertFalse(any(q["resolves"] == "purpose" for q in answered["questions"]))
        self.assertNotIn("plan", answered)
        for name, data in self.before.items():
            self.assertEqual((self.source / name).read_bytes(), data)

    def test_answer_rejects_bad_input(self):
        record = self.prepare()
        purpose = next(q["need"] for q in record["questions"]
                       if q["resolves"] == "purpose" and q["status"] == "user")
        with self.assertRaises(AppError):
            self.state.answer_firmware({"job_id": "nope", "need": purpose, "answer": "x"})
        with self.assertRaises(AppError):
            self.state.answer_firmware({"job_id": record["id"], "need": "nope", "answer": "x"})
        with self.assertRaises(AppError):
            self.state.answer_firmware({"job_id": record["id"], "need": purpose, "answer": "   "})

    def test_dont_know_declines_without_backfill(self):
        record = self.prepare()
        purpose = next(q["need"] for q in record["questions"]
                       if q["resolves"] == "purpose" and q["status"] == "user")
        declined = self.state.answer_firmware(
            {"job_id": record["id"], "need": purpose, "answer": "I don't know"})
        question = next(q for q in declined["questions"] if q["need"] == purpose)
        self.assertEqual(question["status"], "declined")
        self.assertEqual(declined["spec"]["stated_purpose"], "")
        self.assertFalse(declined["answers"])

    def _planned(self):
        record = self.prepare()
        stored = self.state.firmware_jobs[record["id"]]
        stored["spec"]["mcu"] = {"status": "identified", "reference": "U1",
                                 "family": "nRF52", "declared_value": "nRF52840",
                                 "declared_mpn": "nRF52840-QIAA", "package": "",
                                 "confidence": "high", "evidence": []}
        return self.state.plan_firmware({"job_id": record["id"]})

    def test_generate_builds_project_from_plan(self):
        planned = self._planned()
        built = self.state.generate_firmware({"job_id": planned["id"]})
        build = built["build"]
        self.assertIn("src/main.c", build["files"])
        self.assertIn("Makefile", build["files"])
        self.assertIn("README.md", build["files"])
        self.assertIn("firmware.json", build["files"])
        self.assertTrue(all(v["status"] in ("pass", "warn") for v in build["validation"]))
        self.assertTrue(build["stats"]["files"] >= 10)
        second = AppState(str(self.source), self.reports)
        try:
            self.assertIn("src/main.c", second.firmware(planned["id"])["build"]["files"])
        finally:
            second.close()
        for name, data in self.before.items():
            self.assertEqual((self.source / name).read_bytes(), data)

    def test_generate_requires_plan(self):
        record = self.prepare()
        with self.assertRaises(AppError):
            self.state.generate_firmware({"job_id": record["id"]})
        with self.assertRaises(AppError):
            self.state.generate_firmware({"job_id": "nope"})

    def test_generate_blocked_for_unsupported_target(self):
        planned = self._planned()
        stored = self.state.firmware_jobs[planned["id"]]
        stored["spec"]["mcu"] = dict(stored["spec"]["mcu"], family="ESP32",
                                     declared_value="ESP32-S3")
        stored.pop("plan", None)
        planned = self.state.plan_firmware({"job_id": planned["id"]})
        result = self.state.generate_firmware({"job_id": planned["id"]})
        self.assertFalse(result["ok"])
        self.assertIn("ESP32", result["reason"])

    def _generated(self):
        planned = self._planned()
        return self.state.generate_firmware({"job_id": planned["id"]})

    @unittest.skipUnless(shutil.which("arm-none-eabi-gcc"), "cross-compiler not installed")
    def test_compile_verifies_with_real_toolchain(self):
        from pathlib import Path as _Path
        built = self._generated()
        compiled = self.state.compile_firmware({"job_id": built["id"]})
        result = compiled["build"]["compile"]
        self.assertEqual(result["status"], "COMPILED")
        self.assertEqual(len(result["attempts"]), 1)
        self.assertGreater(result["sizes"].get("elf_bytes", 0), 0)
        build_dir = _Path(self.temp.name) / "reports" / ".firmware" / built["id"] / "build"
        self.assertTrue((build_dir / "src" / "main.c").is_file())
        self.assertTrue((build_dir / "compile.json").is_file())
        second = AppState(str(self.source), self.reports)
        try:
            self.assertEqual(second.firmware(built["id"])["build"]["compile"]["status"], "COMPILED")
        finally:
            second.close()

    def test_compile_requires_build(self):
        record = self.prepare()
        with self.assertRaises(AppError):
            self.state.compile_firmware({"job_id": record["id"]})
        with self.assertRaises(AppError):
            self.state.compile_firmware({"job_id": "nope"})

    def test_export_zips_generated_build(self):
        import io
        import zipfile
        built = self._generated()
        archive, filename = self.state.export_firmware(built["id"])
        self.assertTrue(filename.endswith(".zip"))
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            self.assertEqual(sorted(bundle.namelist()), sorted(built["build"]["files"]))
            self.assertEqual(bundle.read("src/main.c").decode(),
                             built["build"]["files"]["src/main.c"])
        for name, data in self.before.items():
            self.assertEqual((self.source / name).read_bytes(), data)

    def test_export_rejects_missing_or_unbuilt(self):
        with self.assertRaises(AppError):
            self.state.export_firmware("nope")
        record = self.prepare()
        with self.assertRaises(AppError):
            self.state.export_firmware(record["id"])

    def test_records_persist_across_restart(self):
        record = self.prepare()
        second = AppState(str(self.source), self.reports)
        try:
            loaded = second.firmware(record['id'])
        finally:
            second.close()
        self.assertEqual(loaded['status'], 'validated')
        self.assertEqual(loaded['spec']['mcu']['status'], 'unidentified')

    def test_artifact_download_serves_saved_build_files(self):
        from pathlib import Path as _Path
        record = self.prepare()
        build_dir = _Path(self.temp.name) / "reports" / ".firmware" / record["id"] / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / "firmware.uf2").write_bytes(b"UF2\x00fake-binary")
        (build_dir / "main.c").write_text("int main(void){return 0;}\n")
        body, content_type, filename = self.state.firmware_artifact(
            {"job_id": record["id"], "name": "firmware.uf2"})
        self.assertEqual(body, b"UF2\x00fake-binary")
        self.assertEqual(content_type, "application/octet-stream")
        self.assertEqual(filename, "firmware.uf2")
        body, content_type, _ = self.state.firmware_artifact(
            {"job_id": record["id"], "name": "main.c"})
        self.assertIn("int main", body.decode())
        self.assertTrue(content_type.startswith("text/plain"))

    def test_artifact_download_rejects_unknown_files(self):
        record = self.prepare()
        with self.assertRaises(AppError):
            self.state.firmware_artifact({"job_id": record["id"], "name": "../../x"})
        with self.assertRaises(AppError):
            self.state.firmware_artifact({"job_id": record["id"], "name": "firmware.bin"})
        with self.assertRaises(AppError):
            self.state.firmware_artifact({"job_id": record["id"], "name": "firmware.uf2"})
        with self.assertRaises(AppError):
            self.state.firmware_artifact({"job_id": "nope", "name": "main.c"})


if __name__ == '__main__':
    unittest.main()
