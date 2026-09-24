"""The persistent build directory of a project: a synced copy of its read-only source."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

# Copied only when the build directory is created; a change recreates it.
CONFIG = ("lakefile.toml", "lakefile.lean", "lake-manifest.json", "lean-toolchain")
# The harness's scratch directory at the build root.
SCRATCH = ".ai_functions"


def files(root: Path) -> Iterator[Path]:
    """Relative paths of the files under ``root``, outside ``.lake`` and ``.git``; symlinked directories are skipped."""
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = sorted(d for d in subdirectories if d not in (".lake", ".git"))
        for name in sorted(names):
            yield Path(directory, name).relative_to(root)


def sync(source: Path | None, build: Path, config: Mapping[str, bytes]) -> None:
    """Make ``build`` mirror ``source``, recreating it when ``config`` changed.

    Lake's outputs and fetched packages (``.lake``), the manifest Lake writes, and
    the scratch directory survive; source files are copied when their size or
    modification time differs, and files removed from the source are deleted.
    """
    stamp = build / SCRATCH / "config"
    digest = hashlib.sha256(json.dumps({name: data.hex() for name, data in config.items()}).encode()).hexdigest()
    if not stamp.is_file() or stamp.read_text() != digest:
        shutil.rmtree(build, ignore_errors=True)
        (build / SCRATCH / "work").mkdir(parents=True)
        for name, data in config.items():
            (build / name).write_bytes(data)
        stamp.write_text(digest)
    if source is None:
        return
    kept = {Path(name) for name in CONFIG}
    for relative in files(source):
        if relative in kept or relative.parts[0] == SCRATCH:
            continue
        kept.add(relative)
        origin, copy = (source / relative).stat(), build / relative
        try:
            current = copy.stat()
            if (current.st_size, current.st_mtime_ns) == (origin.st_size, origin.st_mtime_ns):
                continue
        except FileNotFoundError:
            copy.parent.mkdir(parents=True, exist_ok=True)
        copy.unlink(missing_ok=True)
        shutil.copy2(source / relative, copy)
    for relative in files(build):
        if relative not in kept and relative.parts[0] != SCRATCH:
            (build / relative).unlink()


def modules(source: Path, build: Path) -> dict[str, Path]:
    """The project's built modules and their source files.

    A module is the project's when Lake built it for the root package and a
    source file under ``source`` has its path; dependencies build elsewhere.
    """
    library = build / ".lake" / "build" / "lib" / "lean"
    sources = [relative.with_suffix("").parts for relative in files(source) if relative.suffix == ".lean"]
    found = {}
    for olean in sorted(library.rglob("*.olean")):
        parts = olean.relative_to(library).with_suffix("").parts
        matches = [path for path in sources if path[-len(parts) :] == parts]
        if matches:
            found[".".join(parts)] = source.joinpath(*min(matches, key=len)).with_suffix(".lean")
    return found


def fingerprint(source: Path | None, build: Path, *, identity: str, imports: Sequence[str], prelude: str) -> str:
    """Hash the toolchain identity, imports, prelude, source files and Lake's manifest."""
    digest = hashlib.sha256(json.dumps([identity, list(imports), prelude]).encode())
    if source is not None:
        for relative in files(source):
            digest.update(f"\0{relative}\0".encode() + (source / relative).read_bytes())
    manifest = build / "lake-manifest.json"
    if manifest.is_file():
        digest.update(b"\0lake-manifest.json\0" + manifest.read_bytes())
    return digest.hexdigest()
