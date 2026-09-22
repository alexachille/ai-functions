# Private verified-function runtime

The only public feature is `ai_verified_function`. This directory contains
maintainer tooling and the native boundary used by that decorator.

## Installation and platforms

The `verified` extra installs a pinned compiler distribution and a native
extension through normal Python dependency resolution. Calls never download
compiler tools. End users need no C compiler or Python development headers.

The bridge supports standard CPython 3.12, 3.13, and 3.14 builds with the GIL,
on macOS 15+ and Linux with glibc 2.34+, for ARM64 and x86-64. Each CPython minor
version gets a matching extension wheel. Compiler data wheels are shared by
all three interpreter versions on a platform.

Linux release wheels use the `manylinux_2_34` build images in CI. The pinned
compiler's LLVM libraries need glibc 2.29, so a `manylinux_2_28` tag would
incorrectly admit systems where compilation cannot run.

## Native boundary

`bridge.c` loads verified libraries with `dlopen` and caches a typed C callable.
It invokes only the runtime initializer and exported native entry. Loading a
function does not import serialized compiler environments or interpret code.
The generated `.ffi.c` adapter and `ffi.h` describe the exact argument kinds,
return kind, initializer, and ABI version. The Python loader checks that
signature against the translated specification before accepting the callable.

The compiler checks the generated proof and independently replays the saved
declarations before building or loading the native artifact. The model supplies
only the implementation term and proof; it cannot write the C adapter or change
the exported signature. Linux artifacts link explicitly against the installed
runtime and reject unresolved symbols while linking.

The representation conversions are:

| Python value | Native representation | Conversion |
| --- | --- | --- |
| `bool` | `uint8_t` | Unboxed scalar |
| `float` | `double` | Unboxed binary64; NaNs are canonicalized |
| Small `int` | Immediate integer | No temporary value buffer |
| Large `int` | Runtime big integer | Repack directly between Python digits and native limbs |
| `list[int]` | Immutable native list | One native list construction; output Python list allocated once |

No values pass through text, JSON, decimal conversion, hexadecimal strings, or
intermediate byte buffers. Before validation, Python list arguments get one
shallow copy of their references. The integer objects are retained, not copied.
This snapshot prevents mutation during validation, compilation, or asynchronous
execution from changing the verified input.

The bridge borrows Python values while holding the GIL. Converted native object
arguments are owned and consumed by the generated entry; it can reuse uniquely
owned native storage. The returned native object is converted and released.
The GIL is released during native execution. Foreign threads initialize and
finalize their runtime state through thread-local cleanup. Calls after `fork`
require a fresh process; use `spawn`. The bridge does not support subinterpreters
or free-threaded/debug CPython builds.

Direct big-integer conversion uses the selected CPython headers and the pinned
compiler's GMP object layout. A startup check validates that native layout.
The bridge uses the allocator from the compiler's own shared library. There is
no additional GMP dependency or second allocator. Successfully initialized
libraries remain loaded so runtime constants cannot retain dangling code
pointers; compiler environments are never retained.

## Build and test

Maintainers need a C compiler, system development headers, and `zstandard` for
archive extraction. Python need not provide a shared `libpython`.

```bash
uv python install 3.12 3.13 3.14
uv venv --python 3.12 --managed-python .venv
uv pip install --python .venv/bin/python zstandard pytest pytest-asyncio hypothesis
.venv/bin/python runtime/build.py \
  --python .venv/bin/python \
  --python "$(uv python find 3.13)" \
  --python "$(uv python find 3.14)"
uv pip install --python .venv/bin/python --find-links dist/verified-runtime -e '.[verified]'
AI_FUNCTIONS_REQUIRE_VERIFIED_RUNTIME=1 .venv/bin/python -m pytest -q \
  tests/test_ai_verified_function.py tests/test_verified_contracts.py \
  tests/test_verified_compiler.py tests/test_verified_value_types.py \
  tests/test_verified_ffi.py
```

`--toolchain` and `--cache` reuse existing or offline compiler inputs. The build
verifies downloaded archives, compiles the bridge for each requested Python,
and packages compiler data once. CI runs actual proof checking, native calls,
ABI/ownership tests, and independent Python oracles on each interpreter.
Model responses are scripted, so these tests need no provider credentials.

Run `python runtime/benchmark.py --output verified-benchmark.json` to measure
warm conversion/call overhead separately from the public wrapper's contract
validation. Results include small and large integers, several list sizes, and
peak process memory. Timings are microbenchmarks, not algorithm speedups.

## Distribution

The full compiler payload is split across data wheels below package-index file
limits. Files are installed directly into the private runtime package; there
are no post-install hooks, first-use downloads, or first-use extraction.
`build.py` records download sizes in `sizes-*.json`.

Publish the runtime owner and data wheels before releasing the main package's
`verified` extra. Local installs use `--find-links dist/verified-runtime` until
publication. Bump the runtime version in the builder, compiler module, and main
extra when changing the native ABI; bump `AV_ABI_VERSION` and `FFI_ABI_VERSION`
together when changing the descriptor layout.

The private `AI_FUNCTIONS_VERIFIED_RUNTIME` variable can select a prepared
runtime directory for tests or offline deployments. It never installs tools.

The Python contract translator is deterministic and independent of Strata.
The Strata-Python review found that its importer can skip assignments, returns,
and unsupported assertions, and emits Laurel/Core rather than the required
standalone specification. This implementation rejects unsupported Python
constructs explicitly and preserves the supported validators' semantics.
