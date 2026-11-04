"""PCB-to-Firmware Milestone E: real toolchain verification with a bounded
compile → diagnose → correct loop.

``verify_build`` compiles a generated file set with the real cross-compiler
in an isolated temp directory. On failure it classifies the diagnostics; the
only automatic correction it applies is enabling a missing optional HAL
module and regenerating (via a caller-supplied ``regen`` callback), bounded
to ``max_attempts``. Anything else is FAILED with the parsed log — compiler
errors are never hidden and a failed build is never presented as success.

Statuses: COMPILED (real toolchain exit 0), FAILED (real errors, attempts
exhausted), NOT VERIFIED (toolchain unavailable — with build instructions,
never a fake pass).
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path


GCC_ERROR = re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<col>\d+):\s*"
                       r"(?P<kind>fatal error|error|warning|note):\s*(?P<text>.*)$", re.M)
MISSING_HEADER = re.compile(r"fatal error:\s*(?P<header>[\w./-]+\.h):\s*No such file", re.M)
TOOLCHAIN = "arm-none-eabi-gcc"
COMPILE_TIMEOUT = 120


def _write_project(files, root):
    root = Path(root)
    for name, content in (files or {}).items():
        if not isinstance(name, str) or not isinstance(content, str):
            continue
        target = root / name
        if ".." in Path(name).parts:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def _cmake_available():
    return shutil.which("cmake") or ""


def _parse_diagnostics(log):
    errors = [{"file": m.group("file"), "line": int(m.group("line")),
               "kind": m.group("kind"), "text": m.group("text")[:300]}
              for m in GCC_ERROR.finditer(log) if m.group("kind") in ("error", "fatal error")]
    warnings = [{"file": m.group("file"), "line": int(m.group("line")),
                 "kind": m.group("kind"), "text": m.group("text")[:300]}
                for m in GCC_ERROR.finditer(log) if m.group("kind") == "warning"]
    return errors, warnings


def _run_cmake(workdir, timeout):
    """Configure and build a Pico SDK (CMake) project in an isolated dir.

    CMakeLists.txt selects this path; the Pico SDK (PICO_SDK_PATH) supplies
    startup, linker, boot stage 2 and elf2uf2. Missing cmake or an
    unconfigurable project is reported honestly, never faked.
    """
    cmake = _cmake_available()
    if not cmake:
        return {"ran": False, "ok": False, "reason": "cmake is not installed",
                "log": "", "errors": [], "warnings": []}
    import os
    sdk_import = Path(workdir) / "pico_sdk_import.cmake"
    if not sdk_import.exists():
        sdk = Path(os.environ.get("PICO_SDK_PATH", ""))
        # SDK 1.x kept the import shim at the top level; SDK 2.x moved it
        # to external/. Either way the generated CMakeLists includes it.
        for candidate in (sdk / "pico_sdk_import.cmake",
                          sdk / "external" / "pico_sdk_import.cmake"):
            if candidate.is_file():
                try:
                    shutil.copyfile(candidate, sdk_import)
                    break
                except OSError:
                    pass
    build = Path(workdir) / "build"
    try:
        cfg = subprocess.run([cmake, "-S", str(workdir), "-B", str(build)],
                             capture_output=True, text=True, timeout=timeout,
                             errors="replace")
        if cfg.returncode != 0:
            log = (cfg.stdout or "") + (cfg.stderr or "")
            return {"ran": True, "ok": False, "reason": "cmake configure failed",
                    "log": log[-8000:],
                    "errors": [{"file": "", "line": 0, "kind": "error",
                                "text": log[-1500:]}], "warnings": []}
        done = subprocess.run([cmake, "--build", str(build), "--parallel"],
                              capture_output=True, text=True, timeout=timeout,
                              errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ran": False, "ok": False,
                "reason": "compiler did not run to completion: %s" % type(exc).__name__,
                "log": "", "errors": [], "warnings": []}
    log = (done.stdout or "") + (done.stderr or "")
    errors, warnings = _parse_diagnostics(log)
    return {"ran": True, "ok": bool(done.returncode == 0),
            "log": log[-8000:], "errors": errors, "warnings": warnings,
            "toolchain": shutil.which(TOOLCHAIN) or TOOLCHAIN,
            "version": _toolchain_version(shutil.which(TOOLCHAIN) or TOOLCHAIN)}


def _run_make(workdir, toolchain=TOOLCHAIN, timeout=COMPILE_TIMEOUT):
    binary = shutil.which(toolchain or "")
    if not binary:
        return {"ran": False, "ok": False, "reason": "toolchain %r is not installed" % toolchain,
                "log": "", "errors": [], "warnings": []}
    try:
        done = subprocess.run(["make"], cwd=str(workdir), capture_output=True,
                              text=True, timeout=timeout, errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ran": False, "ok": False,
                "reason": "compiler did not run to completion: %s" % type(exc).__name__,
                "log": "", "errors": [], "warnings": []}
    log = (done.stdout or "") + (done.stderr or "")
    errors, warnings = _parse_diagnostics(log)
    return {"ran": True, "ok": bool(done.returncode == 0),
            "log": log[-8000:], "errors": errors, "warnings": warnings,
            "toolchain": binary,
            "version": _toolchain_version(binary)}


def _build_project(workdir, toolchain=TOOLCHAIN, timeout=COMPILE_TIMEOUT):
    """Build with CMake when the project ships CMakeLists.txt, else make."""
    if (Path(workdir) / "CMakeLists.txt").exists():
        return _run_cmake(workdir, timeout)
    return _run_make(workdir, toolchain=toolchain, timeout=timeout)


def _toolchain_version(binary):
    try:
        done = subprocess.run([binary, "--version"], capture_output=True,
                              text=True, timeout=15, errors="replace")
        first = (done.stdout or "").splitlines()
        return first[0].strip()[:160] if first else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _keep_artifacts(workdir, artifact_dir):
    """Copy built binaries aside; names are fixed by the generators."""
    kept = []
    if not artifact_dir:
        return kept
    try:
        dest = Path(artifact_dir)
        dest.mkdir(parents=True, exist_ok=True)
    except OSError:
        return kept
    for name in ("firmware.elf", "firmware.hex", "firmware.uf2"):
        try:
            data = (Path(workdir) / "build" / name).read_bytes()
        except OSError:
            continue
        if not data:
            continue
        try:
            (dest / name).write_bytes(data)
            kept.append(name)
        except OSError:
            continue
    return kept


def _sizes(workdir):
    out = {}
    for name, key in (("build/firmware.elf", "elf_bytes"),
                      ("build/firmware.hex", "hex_bytes"),
                      ("build/firmware.uf2", "uf2_bytes")):
        try:
            out[key] = (Path(workdir) / name).stat().st_size
        except OSError:
            pass
    return out


def _missing_hal_module(log):
    """If the failure is a missing optional HAL header, name its module."""
    match = MISSING_HEADER.search(log or "")
    if not match:
        return ""
    header = match.group("header")
    if header in ("hal_gpio.h", "hal_i2c.h"):
        return header[:-2]
    return ""


def verify_build(files, regen=None, max_attempts=3, toolchain=TOOLCHAIN,
                 timeout=COMPILE_TIMEOUT, artifact_dir=None):
    """Compile, repair once per new fact, and report honestly.

    ``regen(modules_to_add)`` rebuilds the file set when the diagnosis
    identifies a missing optional HAL module. Every attempt is recorded.
    When ``artifact_dir`` is given, a COMPILED build copies its binary
    artifacts (elf/hex/uf2) there so the application can serve downloads.
    """
    attempts = []
    current = dict(files or {})
    added_modules = []
    for attempt in range(1, max_attempts + 1):
        with tempfile.TemporaryDirectory(prefix="preflight-fw-") as tmp:
            _write_project(current, tmp)
            result = _build_project(tmp, toolchain=toolchain, timeout=timeout)
            entry = {"attempt": attempt, "ok": result.get("ok", False),
                     "added_modules": list(added_modules),
                     "errors": result.get("errors", [])[:10],
                     "warnings": len(result.get("warnings", []))}
            if not result.get("ran"):
                entry["note"] = result.get("reason", "")
                attempts.append(entry)
                return {"status": "NOT VERIFIED", "attempts": attempts,
                        "log": "", "sizes": {}, "toolchain": toolchain,
                        "files": current,
                        "reason": result.get("reason", "")}
            entry.update(_sizes(tmp))
            if result.get("toolchain"):
                entry["toolchain"] = result["toolchain"]
            attempts.append(entry)
            if result["ok"]:
                artifacts = _keep_artifacts(tmp, artifact_dir)
                return {"status": "COMPILED", "attempts": attempts,
                        "log": result.get("log", ""),
                        "sizes": {k: v for k, v in entry.items()
                                  if k.endswith("_bytes")},
                        "artifacts": artifacts,
                        "toolchain": result.get("toolchain", ""),
                        "version": result.get("version", ""),
                        "files": current}
            module = _missing_hal_module(result.get("log", ""))
            if module and regen is not None and module not in added_modules:
                added_modules.append(module)
                current = dict(regen(list(added_modules)) or current)
                continue
            return {"status": "FAILED", "attempts": attempts,
                    "log": result.get("log", ""),
                    "sizes": {}, "toolchain": result.get("toolchain", ""),
                    "files": current,
                    "reason": "; ".join(e["text"] for e in entry["errors"][:3]) or "build failed"}
    return {"status": "FAILED", "attempts": attempts,
            "log": "", "sizes": {}, "toolchain": toolchain,
            "files": current,
            "reason": "correction loop exhausted without new evidence"}


def compile_report_markdown(report):
    """Short human-readable summary of a verification report."""
    if report.get("status") == "COMPILED":
        sizes = report.get("sizes", {})
        if sizes.get("uf2_bytes"):
            return "COMPILED with %s (%s bytes elf, %s bytes uf2)." % (
                report.get("toolchain", "toolchain"),
                sizes.get("elf_bytes", "?"), sizes.get("uf2_bytes", "?"))
        return "COMPILED with %s (%s bytes elf)." % (
            report.get("toolchain", "toolchain"),
            sizes.get("elf_bytes", "?"))
    if report.get("status") == "NOT VERIFIED":
        return "NOT VERIFIED: %s." % report.get("reason", "toolchain unavailable")
    return "FAILED: %s." % report.get("reason", "build errors")
