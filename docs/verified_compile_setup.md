# Experimental verified compilation: setup and development

Install `strands-ai-functions` normally, then import:

```python
from ai_functions.experimental.lean import LeanConfig
from ai_functions.experimental.verified_compile import verified_ai_compile
```

There is no `verified` extra or separately installed runtime package. The
platform-independent Python wheel contains the bridge's C sources and header.
Compilation builds native artifacts locally; importing the package does not
initialize Lean, download tools, or build native code.

## Toolchain and host prerequisites

The supported Lean release is **4.33.1**. `LeanConfig().setup()` first reuses an exact
matching installation, then the library's managed cache, and provisions missing
tools through an isolated, checksum-verified elan bootstrap. It does not change
the user's elan configuration or shell. `LeanConfig(mode="system")` requires
an installed release; `mode="managed"` only uses the library's managed cache.

```python
config = LeanConfig(cache_dir="/path/to/shared/lean-cache")
config.setup()                 # Explicit provisioning before application execution
config.setup(offline=True)     # Reuse installed/cached tools; never download
```

Pass `lean_config=config` and `offline=True` to the decorator to reuse that selection.
Alternatively, `AI_FUNCTIONS_LEAN_TOOLCHAIN_MODE` and
`AI_FUNCTIONS_LEAN_CACHE_DIR` configure its defaults. Offline refers to compiler
setup; synthesis still needs its configured model unless a compiled artifact is
already available. Call the decorated function's `compile_sync()` during
preparation to build everything required for later cached calls.

Native execution supports standard CPython 3.12–3.14 with the GIL, on macOS 15+
and Linux, ARM64 and x86-64. Linux CI runs on Ubuntu 24.04; compatibility follows
the selected official Lean distribution's system requirements. Debug builds,
free-threaded builds, and subinterpreters remain unsupported.

The selected release must include `lean`, `leanchecker`, `leanc`, its headers,
and shared runtime libraries. The running Python must supply `Python.h`,
`pyconfig.h`, and `cpython/longintrepr.h`. Some operating-system Python packages
require matching development-header packages. On macOS, install the Command Line
Tools or Xcode so `xcrun` can locate the SDK. Compilation uses Lean's `leanc`;
the local host SDK/linker must also work.

Before any model request, setup builds and loads the bridge, reporting missing
headers, SDKs, incompatible layouts, or linker failures. Internal exceptions
expose `diagnostics` for the underlying compiler output.

## Native boundary and caches

The original direct CPython/Lean bridge is preserved. Booleans and floats pass as
native scalars; arbitrary-precision integers are converted directly between
Python digits and Lean/GMP storage; lists use native Lean lists. No JSON or other
serialization is introduced. NaNs retain their canonicalization policy, and
floating-point builds disable fused contraction.

Bridge artifacts live under the shared cache's `lean/bridges` directory and are
reused across functions. Their identity includes the exact Lean installation,
Python ABI/version, source hashes, and compiler/linker settings. Function
artifacts live in a separate `verified_compile` cache, selectable through the
decorator's `cache_dir`. They additionally identify the translated contracts
and implementation pipeline.

Both caches use process locks, hashed manifests, and atomic publication.
Incomplete or corrupted artifacts are rebuilt. Keep the selected toolchain
installation available: native libraries reference its shared runtime paths.
Changing the toolchain, bridge sources, or Python ABI selects a new cache entry.
A running process cannot switch its loaded bridge/toolchain; restart Python.

The model writes only the implementation and proof terms. The original axiom
audit and independent `leanchecker` replay precede native compilation and
publication. Runtime checks use translated predicate IR, never the original
Python function or validator bodies.

Native calls release the GIL and preserve thread initialization/finalization,
object ownership, and loaded-library lifetime handling. After `fork`, use a new
process via `spawn`; inherited native callables remain invalid.

## Development checks

```bash
hatch run python -c 'from ai_functions.experimental.lean import LeanConfig; LeanConfig().setup()'
AI_FUNCTIONS_REQUIRE_VERIFIED_NATIVE=1 hatch run test \
  tests/test_lean_*.py tests/test_verified_runtime.py \
  tests/test_verified_ai_compile.py tests/test_verified_compiler.py \
  tests/test_verified_contracts.py tests/test_verified_value_types.py \
  tests/test_verified_ffi.py
hatch run python examples/verified_benchmark.py --output verified-benchmark.json
```

Native CI covers source and wheel installs on macOS/Linux, ARM64/x86-64, and
CPython 3.12–3.14. Missing prerequisites fail native CI instead of silently
skipping it. Benchmark reports separate direct-call latency, public-wrapper
latency, and peak process memory.

## Shared module boundaries

`ai_functions.experimental.lean` exports `LeanConfig` and `ResolvedToolchain`.
Both live in `toolchain.py`: `LeanConfig.setup()` returns the resolved installation,
which owns executable paths, environment construction, and lazy native build
settings. The verified compiler uses that installation directly.

Other Lean consumers use the public `execution` and `locking` modules. Execution
owns synchronous/asynchronous process runners, process cleanup, and draining
blocking work on cancellation. Locking owns both synchronous and asynchronous
cache locks. These shared modules have matching public type specifications.
Only `_provision.py` is private: its download and installation internals are
called by `LeanConfig.setup()`, not by sibling feature modules.
