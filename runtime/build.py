"""Build installable compiler data and a typed CPython native bridge.

Maintainer/CI tooling only. End users install the resulting wheels through the
normal Python dependency resolver. No downloads or build hooks run on a user's
first verified-function call.

The large compiler payload is split across ordinary data wheels so individual
uploads stay below package-index file limits. Every wheel owns distinct files
under the same private package; pip installs them directly, without post-install
scripts or first-use extraction. Small interpreter-specific wheels depend on
the exact data-wheel versions and contain prebuilt native bridges.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = "0.2.0"
PACKAGE = "_ai_functions_verified_runtime"
DIST = "strands_ai_functions_verified_runtime"
HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "toolchains.json").read_text())


def sha256(path: Path) -> str:
    """Hash an archive without holding it in memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url: str, destination: Path, expected: str) -> None:
    """Download a pinned archive, verify it, and publish it atomically."""
    if destination.is_file() and sha256(destination) == expected:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    print(f"Downloading {destination.name}", flush=True)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "ai-functions-runtime-builder"})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        if sha256(temporary) != expected:
            raise RuntimeError(f"Integrity check failed for {destination.name}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def unpack(archive: Path, destination: Path, root_name: str) -> Path:
    """Safely extract a pinned archive outside the checkout."""
    root = destination / root_name
    if (root / ".runtime-builder-complete").is_file():
        return root
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="extract-", dir=destination) as temporary:
        if archive.name.endswith(".zst"):
            import zstandard

            with archive.open("rb") as compressed, zstandard.ZstdDecompressor().stream_reader(compressed) as stream:
                with tarfile.open(fileobj=stream, mode="r|") as bundle:
                    bundle.extractall(temporary, filter="data")
        else:
            with tarfile.open(archive) as bundle:
                bundle.extractall(temporary, filter="data")
        extracted = Path(temporary) / root_name
        (extracted / ".runtime-builder-complete").touch()
        if root.exists():
            shutil.rmtree(root)
        extracted.rename(root)
    return root


def compiler_environment(toolchain: Path) -> dict[str, str]:
    """Pin build-tool discovery without changing the user's shell or global setup."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LEAN", "LAKE"))}
    env.update(
        {
            "PATH": str(toolchain / "bin") + os.pathsep + os.environ.get("PATH", ""),
            "LEAN_SYSROOT": str(toolchain),
        }
    )
    if sys.platform == "darwin":
        env["MACOSX_DEPLOYMENT_TARGET"] = "15.0"
    return env


def build_bridge(toolchain: Path, workspace: Path, python: str) -> tuple[Path, str]:
    """Build against the selected CPython headers without linking libpython."""
    query = """import json, platform, sys, sysconfig
print(json.dumps({
    'version': list(sys.version_info[:2]), 'implementation': sys.implementation.name,
    'include': sysconfig.get_path('include'), 'suffix': sysconfig.get_config_var('EXT_SUFFIX'),
    'free_threaded': bool(sysconfig.get_config_var('Py_GIL_DISABLED')),
    'debug': bool(sysconfig.get_config_var('Py_DEBUG')),
    'platform': sys.platform, 'machine': platform.machine(),
}))
"""
    config = json.loads(subprocess.check_output([python, "-c", query], text=True))
    if config["implementation"] != "cpython" or tuple(config["version"]) < (3, 12):
        raise RuntimeError("Build the bridge for CPython 3.12 or newer")
    if config["free_threaded"] or config["debug"]:
        raise RuntimeError("The native bridge requires a standard CPython build with the GIL")
    if (config["platform"], config["machine"]) != (sys.platform, platform.machine()):
        raise RuntimeError("The build interpreter and toolchain must target the host architecture")
    interpreter = "cp" + "".join(map(str, config["version"]))
    directory = workspace / interpreter
    directory.mkdir(parents=True, exist_ok=True)
    extension = directory / ("_bridge" + config["suffix"])
    command = [
        os.environ.get("CC", "cc"),
        "-shared",
        "-fPIC",
        "-O3",
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-Werror=implicit-function-declaration",
        "-I",
        config["include"],
        "-I",
        str(toolchain / "include"),
        "-I",
        str(HERE),
        str(HERE / "bridge.c"),
        "-L",
        str(toolchain / "lib" / "lean"),
        "-lleanshared",
        "-lInit_shared",
        "-o",
        str(extension),
    ]
    if sys.platform == "darwin":
        command += [
            "-undefined",
            "dynamic_lookup",
            "-mmacosx-version-min=15.0",
            "-Wl,-rpath,@loader_path/toolchain/lib/lean",
            "-Wl,-rpath,@loader_path/toolchain/lib",
        ]
    else:
        command += ["-Wl,-rpath,$ORIGIN/toolchain/lib/lean:$ORIGIN/toolchain/lib"]
    subprocess.run(command, env=compiler_environment(toolchain), check=True)
    if sys.platform == "darwin":
        subprocess.run(["codesign", "--force", "--sign", "-", str(extension)], check=True)
    return extension, f"{interpreter}-{interpreter}"


def write_wheel(
    output: Path,
    name: str,
    tag: str,
    files: list[tuple[Path, str]],
    contents: dict[str, bytes] | None = None,
    dependencies: list[str] | None = None,
) -> Path:
    """Write a standards-compliant wheel with hashes, sizes and file permissions."""
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{name}-{VERSION}-{tag}.whl"
    metadata_dir = f"{name}-{VERSION}.dist-info"
    records = []
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as wheel:
        for source, target in files:
            digest = hashlib.sha256()
            size = 0
            info = zipfile.ZipInfo.from_file(source, arcname=target)
            info.compress_type = zipfile.ZIP_DEFLATED
            with source.open("rb") as stream, wheel.open(info, "w", force_zip64=True) as member:
                while chunk := stream.read(1024 * 1024):
                    member.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            encoded = base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode()
            records.append((target, "sha256=" + encoded, str(size)))
        metadata = (
            f"Metadata-Version: 2.4\nName: {name.replace('_', '-')}\nVersion: {VERSION}\n"
            "Summary: Private native runtime for ai_verified_function\nRequires-Python: >=3.12\n"
            "License: See bundled upstream license notices\n"
        )
        for dependency in dependencies or []:
            metadata += f"Requires-Dist: {dependency.replace('_', '-')}=={VERSION}\n"
        extra = {
            f"{metadata_dir}/METADATA": metadata.encode(),
            f"{metadata_dir}/WHEEL": (
                f"Wheel-Version: 1.0\nGenerator: ai-functions-runtime-builder\nRoot-Is-Purelib: false\nTag: {tag}\n"
            ).encode(),
            **(contents or {}),
        }
        for target, data in extra.items():
            wheel.writestr(target, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            records.append((target, "sha256=" + digest, str(len(data))))
        buffer = io.StringIO(newline="")
        csv.writer(buffer).writerows([*records, (f"{metadata_dir}/RECORD", "", "")])
        wheel.writestr(f"{metadata_dir}/RECORD", buffer.getvalue())
    if path.stat().st_size > 95 * 1024**2:
        raise RuntimeError(f"{path.name} exceeds the upload limit; rebuild with a smaller --chunk-mib")
    print(f"Built {path.name}: {path.stat().st_size / 1024**2:.1f} MiB", flush=True)
    return path


def package_runtime(
    toolchain: Path,
    bridges: list[tuple[Path, str]],
    output: Path,
    wheel_platform: str,
    chunk_mib: int,
) -> list[Path]:
    """Package compiler data once and a native bridge for each interpreter."""
    groups: list[list[tuple[Path, str]]] = [[]]
    size = 0
    for path in sorted(toolchain.rglob("*")):
        if not path.is_file() or path.name == ".runtime-builder-complete":
            continue
        length = path.stat().st_size
        if size and size + length > chunk_mib * 1024**2:
            groups.append([])
            size = 0
        target = f"{PACKAGE}/toolchain/{path.relative_to(toolchain).as_posix()}"
        groups[-1].append((path, target))
        size += length
    names = [f"{DIST}_data_{i:02}" for i in range(len(groups))]
    wheels = [
        write_wheel(output, name, f"py3-none-{wheel_platform}", files)
        for name, files in zip(names, groups, strict=True)
    ]
    info = {
        "version": VERSION,
        "lean_version": MANIFEST["version"],
        "ffi_abi": 1,
        "platform": wheel_platform,
    }
    for extension, tag in bridges:
        wheels.append(
            write_wheel(
                output,
                DIST,
                f"{tag}-{wheel_platform}",
                [(extension, f"{PACKAGE}/{extension.name}"), (HERE / "ffi.h", f"{PACKAGE}/ffi.h")],
                contents={
                    f"{PACKAGE}/__init__.py": b'"""Private runtime data. Use ai_functions.ai_verified_function."""\n',
                    f"{PACKAGE}/runtime.json": json.dumps(info, sort_keys=True).encode(),
                    f"{PACKAGE}/licenses/ai-functions.LICENSE": (HERE.parent / "LICENSE").read_bytes(),
                },
                dependencies=names,
            )
        )
    report = {
        "platform": wheel_platform,
        "wheels": len(wheels),
        "download_bytes": sum(p.stat().st_size for p in wheels),
    }
    (output / f"sizes-{wheel_platform}.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
    return wheels


def main() -> None:
    """Build the private runtime for the current macOS/Linux architecture."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE.parent / "dist" / "verified-runtime")
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "ai-functions-runtime-build")
    parser.add_argument("--toolchain", type=Path, help="Use an existing pinned toolchain (maintainer/offline builds)")
    parser.add_argument(
        "--python", action="append", help="CPython 3.12+ executable; repeat to build multiple interpreter wheels"
    )
    parser.add_argument("--chunk-mib", type=int, default=128)
    args = parser.parse_args()
    if sys.version_info < (3, 12):  # noqa: UP036 - the standalone builder can be run outside package installation
        parser.error("Build the runtime with Python 3.12 or newer")
    if args.chunk_mib <= 0:
        parser.error("--chunk-mib must be positive")
    key = f"{sys.platform}-{platform.machine()}"
    if key not in MANIFEST["archives"]:
        parser.error(f"No runtime build is configured for {key}")
    if shutil.which(os.environ.get("CC", "cc")) is None:
        parser.error(
            "Building the private runtime needs a C compiler and development headers "
            "(build-essential on Debian/Ubuntu, or a manylinux build image). "
            "End users install prebuilt runtime wheels and do not need these tools."
        )
    config = MANIFEST["archives"][key]
    cache = args.cache.expanduser().resolve()
    if args.toolchain:
        toolchain = args.toolchain.expanduser().resolve()
    else:
        archive = cache / (config["name"] + ".tar.zst")
        download(
            f"https://github.com/leanprover/lean4/releases/download/v{MANIFEST['version']}/{archive.name}",
            archive,
            config["sha256"],
        )
        toolchain = unpack(archive, cache, config["name"])
    version = subprocess.check_output([str(toolchain / "bin" / "lean"), "--version"], text=True)
    if f"version {MANIFEST['version']}," not in version:
        parser.error("The build toolchain does not match the pinned version")
    bridges = [build_bridge(toolchain, cache / f"bridge-{key}", python) for python in args.python or [sys.executable]]
    package_runtime(toolchain, bridges, args.output.resolve(), config["wheel_platform"], args.chunk_mib)


if __name__ == "__main__":
    main()
