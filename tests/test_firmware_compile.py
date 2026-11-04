import shutil
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.firmware.compile import (
    verify_build, compile_report_markdown, TOOLCHAIN)
from pcb_checker.firmware.generator import generate_firmware
from test_firmware_plan import spec as plan_spec
from test_firmware_generate import gen_spec

HAVE_TOOLCHAIN = shutil.which(TOOLCHAIN) is not None


def project(modules=("hal_gpio", "hal_i2c")):
    inner = plan_spec()
    inner["modules"] = list(modules)
    inner["drivers"] = []
    return generate_firmware(gen_spec(), inner)["files"]


class CompileTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_TOOLCHAIN, "cross-compiler not installed")
    def test_generated_project_compiles_first_try(self):
        files = project()
        report = verify_build(files)
        self.assertEqual(report["status"], "COMPILED")
        self.assertEqual(len(report["attempts"]), 1)
        self.assertGreater(report["sizes"].get("elf_bytes", 0), 0)
        self.assertIn("COMPILED", compile_report_markdown(report))

    @unittest.skipUnless(HAVE_TOOLCHAIN, "cross-compiler not installed")
    def test_compiled_artifacts_kept_only_with_artifact_dir(self):
        import tempfile
        from pathlib import Path as _Path
        files = project()
        plain = verify_build(files)
        self.assertEqual(plain["status"], "COMPILED")
        self.assertEqual(plain.get("artifacts", []), [])
        with tempfile.TemporaryDirectory(prefix="fw-art-") as keep:
            kept = verify_build(files, artifact_dir=keep)
            self.assertEqual(kept["status"], "COMPILED")
            self.assertEqual(sorted(kept.get("artifacts", [])),
                             ["firmware.elf", "firmware.hex"])
            for name in kept["artifacts"]:
                self.assertGreater((_Path(keep) / name).stat().st_size, 0)
            self.assertIn("COMPILED", compile_report_markdown(kept))
            self.assertNotIn("uf2", compile_report_markdown(kept))

    @unittest.skipUnless(HAVE_TOOLCHAIN, "cross-compiler not installed")
    def test_broken_build_fails_with_parsed_errors(self):
        files = dict(project())
        files["src/main.c"] = '#include "no_such_header.h"\nint main(void){return 0;}\n'
        report = verify_build(files)
        self.assertEqual(report["status"], "FAILED")
        self.assertTrue(report["attempts"][0]["errors"])
        self.assertIn("FAILED", compile_report_markdown(report))

    @unittest.skipUnless(HAVE_TOOLCHAIN, "cross-compiler not installed")
    def test_missing_hal_triggers_one_regeneration(self):
        full = project(("hal_gpio", "hal_i2c"))
        broken = {k: v for k, v in full.items() if k not in ("src/hal_i2c.h", "src/hal_i2c.c")}
        seen = []

        def regen(modules):
            seen.append(list(modules))
            inner = plan_spec()
            inner["modules"] = ["hal_gpio"] + [m for m in modules if m != "hal_gpio"]
            inner["drivers"] = []
            return generate_firmware(gen_spec(), inner)["files"]

        report = verify_build(broken, regen=regen, max_attempts=3)
        self.assertEqual(report["status"], "COMPILED")
        self.assertEqual(len(report["attempts"]), 2)
        self.assertIn("hal_i2c", seen[0])

    def test_missing_toolchain_is_not_verified_never_pass(self):
        files = project()
        report = verify_build(files, toolchain="no-such-gcc-xyz")
        self.assertEqual(report["status"], "NOT VERIFIED")
        self.assertIn("not installed", report["reason"])
        self.assertIn("NOT VERIFIED", compile_report_markdown(report))

    @unittest.skipUnless(HAVE_TOOLCHAIN, "cross-compiler not installed")
    def test_unfixable_error_stops_after_first_attempt(self):
        files = dict(project())
        files["src/main.c"] = "int main(void){ this is not C !!! }\n"
        report = verify_build(files)
        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(len(report["attempts"]), 1)


if __name__ == "__main__":
    unittest.main()
