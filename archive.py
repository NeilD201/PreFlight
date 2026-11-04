"""Bounded ZIP import. Nothing is extracted onto the user's filesystem."""

import stat
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from .discovery import identify_file
from .models import Issue, Project, ProjectFile


class ArchiveError(ValueError):
    """The archive cannot be imported safely or is damaged."""


MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 250 * 1024 * 1024
MAX_ENTRIES = 5000
SAMPLE_BYTES = 64 * 1024


def _validate_entries(entries: list[zipfile.ZipInfo]) -> None:
    if len(entries) > MAX_ENTRIES:
        raise ArchiveError(f"Archive exceeds {MAX_ENTRIES} entries.")
    total = 0
    seen = set()
    for entry in entries:
        name = entry.filename
        path = PurePosixPath(name)
        if (not name or name != entry.orig_filename or path.is_absolute()
                or ".." in path.parts or "\\" in name or ":" in name):
            raise ArchiveError(f"Unsafe archive path: {name!r}")
        key = str(path).casefold()
        if key in seen:
            raise ArchiveError(f"Duplicate archive path: {name!r}")
        seen.add(key)
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ArchiveError(f"Links and special files are unsupported: {name!r}")
        if entry.flag_bits & 1:
            raise ArchiveError(f"Encrypted file is unsupported: {name!r}")
        total += entry.file_size
        if entry.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ArchiveError("Archive exceeds the uncompressed size limits.")


def _read_sample(archive: zipfile.ZipFile, entry: zipfile.ZipInfo) -> bytes:
    # Read through EOF to verify CRC and enforce the limit on actual output.
    sample = bytearray()
    count = 0
    with archive.open(entry) as stream:
        while chunk := stream.read(SAMPLE_BYTES):
            count += len(chunk)
            if count > MAX_FILE_BYTES:
                raise ArchiveError(f"File exceeds the size limit: {entry.filename!r}")
            sample.extend(chunk[:max(0, SAMPLE_BYTES - len(sample))])
    return bytes(sample)


def import_project(path: str | Path) -> Project:
    path = Path(path)
    if path.is_dir():
        return import_directory(path)
    project = Project(archive=path.name)
    try:
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ArchiveError("ZIP exceeds the 100 MiB upload limit.")
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            _validate_entries(entries)
            for entry in entries:
                if entry.is_dir():
                    continue
                sample = _read_sample(archive, entry)
                parts = PurePosixPath(entry.filename).parts
                if "__MACOSX" in parts or parts[-1] == ".DS_Store":
                    continue
                project.files.append(ProjectFile(
                    entry.filename, identify_file(entry.filename, sample), entry.file_size,
                ))
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise ArchiveError(f"Cannot import ZIP: {exc}") from exc
    project.files.sort(key=lambda item: item.path)
    for kind in ("schematic", "pcb"):
        files = [item.path for item in project.files if item.kind == kind]
        if not files:
            project.issues.append(Issue("info", f"MISSING_{kind.upper()}",
                                        f"No KiCad {kind} found; its checks cannot run."))
    boards = [item.path for item in project.files if item.kind == "pcb"]
    if len(boards) > 1:
        project.issues.append(Issue("warning", "MULTIPLE_BOARDS",
                                    "Multiple boards found; select a board before merging components.", boards))
    project.issues.append(Issue("info", "VERIFICATION_NOT_PERFORMED",
                                "Files were inventoried only. Circuit, assembly, and manufacturing checks have not run."))
    return project


def import_directory(path: str | Path) -> Project:
    """Inventory an extracted KiCad project folder without modifying it."""
    path = Path(path)
    if not path.is_dir():
        raise ArchiveError(f"Project folder does not exist: {path}")
    project = Project(archive=path.name)
    total = 0
    for count, item in enumerate(path.rglob("*"), start=1):
        if count > MAX_ENTRIES:
            raise ArchiveError(f"Project exceeds {MAX_ENTRIES} entries.")
        if item.is_symlink():
            raise ArchiveError(f"Links are unsupported: {item.relative_to(path)}")
        if not item.is_file():
            continue
        relative = item.relative_to(path).as_posix()
        if any(part == "__MACOSX" for part in PurePosixPath(relative).parts) or item.name == ".DS_Store":
            continue
        try:
            size = item.stat().st_size
            total += size
            if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ArchiveError(f"Project exceeds the size limits while reading: {relative!r}")
            with item.open("rb") as stream:
                sample = stream.read(SAMPLE_BYTES)
        except OSError as exc:
            raise ArchiveError(f"Cannot read project file {relative!r}: {exc}") from exc
        project.files.append(ProjectFile(relative, identify_file(relative, sample), size))
    project.files.sort(key=lambda item: item.path)
    for kind in ("schematic", "pcb"):
        if not any(item.kind == kind for item in project.files):
            project.issues.append(Issue("info", f"MISSING_{kind.upper()}",
                                        f"No KiCad {kind} found; its checks cannot run."))
    boards = [item.path for item in project.files if item.kind == "pcb"]
    if len(boards) > 1:
        project.issues.append(Issue("warning", "MULTIPLE_BOARDS",
                                    "Multiple boards found; select a board before merging components.", boards))
    project.issues.append(Issue("info", "VERIFICATION_NOT_PERFORMED",
                                "Files were inventoried only. Circuit, assembly, and manufacturing checks have not run."))
    return project


def read_project_file(source: str | Path, relative: str) -> bytes:
    """Read one inventoried file with the same boundaries as the import step."""
    source = Path(source)
    member = PurePosixPath(relative)
    if (not member.parts or member.is_absolute() or ".." in member.parts
            or "\\" in relative or ":" in relative):
        raise ArchiveError(f"Unsafe project path: {relative!r}")
    try:
        if source.is_dir():
            file = source.joinpath(*member.parts)
            if not file.resolve().is_relative_to(source.resolve()):
                raise ArchiveError(f"File is outside the project: {relative!r}")
            if not file.is_file() or file.is_symlink():
                raise ArchiveError(f"Not a regular project file: {relative!r}")
            with file.open("rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
        else:
            if source.stat().st_size > MAX_ARCHIVE_BYTES:
                raise ArchiveError("ZIP exceeds the 100 MiB upload limit.")
            with zipfile.ZipFile(source) as archive:
                _validate_entries(archive.infolist())
                with archive.open(relative) as stream:
                    data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ArchiveError(f"File exceeds the size limit: {relative!r}")
        return data
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise ArchiveError(f"Cannot read {relative!r}: {exc}") from exc
