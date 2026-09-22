# Verified native functions

`ai_verified_function` synthesizes one native implementation from Python
contracts, checks a proof that it satisfies those contracts for every valid
input, and caches the result. Subsequent calls execute the compiled function
without contacting a model.

## Install

Use CPython 3.12 or newer with the GIL enabled. The initial runtime wheels target
macOS 15+ and Linux with glibc 2.34+, on x86-64 and ARM64. Other library features,
including `ai_function`, continue to support Python 3.12+.

```bash
pip install 'strands-ai-functions[verified]'
```

Python setup installs the private compiler runtime as a dependency. It is a
substantial download, split into data wheels that the package manager installs
and caches normally. Importing the package and calling or compiling a function
never downloads compiler tools. No separate compiler commands or development
headers are needed.

New synthesis uses the same model providers as `ai_function`; pass `model=` to
choose one. An existing compiled artifact can be used without model access.

The default provider is Amazon Bedrock. Compiler installation does not configure
AWS credentials. For a named, authenticated profile, run the example with:

```bash
AWS_PROFILE=my-bedrock-profile hatch run verified:python examples/verified_clamp.py
```

Replace `my-bedrock-profile` with your profile name. Missing credentials fail
directly with setup guidance and do not consume verification retries.

Model selection is explicit when desired:

```python
@ai_verified_function(
    model="global.anthropic.claude-opus-5",
    post_conditions=[check_result],
)
def function(x: int) -> int:
    """Describe the desired result."""
```

A string selects a Bedrock model. A Strands `Model` instance selects a provider
and its settings. Omitting `model` uses `global.anthropic.claude-opus-5`, with a
16,384-token output budget and a 300-second network read timeout. Passing a
configured `BedrockModel` lets you change those settings explicitly.
The current synthesis path uses a Strands model with structured output, followed
by this library's checking and retry loop. The separate `CodexAgent` and
`ClaudeAgent` adapters are not currently synthesis backends for this decorator.

## Define Python contracts

```python
from ai_functions import ai_verified_function


def valid_bounds(lo: int, hi: int):
    assert lo <= hi


def check_clamp(result: int, x: int, lo: int, hi: int):
    assert lo <= result <= hi
    if x < lo:
        assert result == lo
    elif x > hi:
        assert result == hi
    else:
        assert result == x


@ai_verified_function(
    pre_conditions=[valid_bounds],
    post_conditions=[check_clamp],
    max_attempts=3,
)
def clamp(x: int, lo: int, hi: int) -> int:
    """Clamp x to the inclusive interval [lo, hi]."""


assert clamp.run_sync(12, 0, 10) == 10
assert clamp.run_sync(-5, 0, 10) == 0  # Reuses the compiled implementation.
```

The contracts define correctness. The docstring provides synthesis guidance;
the decorated Python body is not executed. Contracts are translated
deterministically without a model and without executing the validator functions.

Preconditions receive matching input arguments by name. Postconditions receive
the result as their first positional argument and matching inputs by name, as
with `ai_function`. Unannotated validator parameters inherit the decorated
function's types. At least one postcondition is required.

Assertions and early returns retain their control-flow meaning. A successful
`None` return passes, while an assertion failure or supported explicit `raise`
fails. The existing result-object form is also supported:

```python
from ai_functions.ai_thread import PostConditionResult


def nonnegative(result: int):
    return PostConditionResult(passed=result >= 0, message="Expected a nonnegative result")
```

Contracts remain enforced when Python runs with `-O`.

## Specify properties instead of an algorithm

The [median example](../examples/verified_median.py) specifies three properties:
the result is one of the inputs, at least two inputs are at most the result, and
at least two inputs are at least the result. Ties are included. These properties
allow an implementation based on comparisons, a sorting network, or min/max
operations; the contract does not prescribe the computation.

```python
def is_median(result: int, a: int, b: int, c: int):
    assert result == a or result == b or result == c
    assert (a <= result and b <= result) or (a <= result and c <= result) or (b <= result and c <= result)
    assert (a >= result and b >= result) or (a >= result and c >= result) or (b >= result and c >= result)
```

Run it with an authenticated profile:

```bash
AWS_PROFILE=my-bedrock-profile hatch run verified:python examples/verified_median.py
```

## Compile explicitly or on first use

```python
await clamp.compile()       # Optional: prepare the implementation at startup.
result = await clamp(12, 0, 10)

clamp.compile_sync()         # Equivalent preparation from synchronous code.
result = clamp.run_sync(12, 0, 10)
```

Both compile methods are idempotent and return the decorated function. Without
an explicit compile call, the first valid function call performs compilation.
`clamp.is_compiled` reports whether this object has resolved a compiled artifact.

The implementation is verified for all inputs satisfying the preconditions,
rather than specialized to the first call's values. Preconditions are checked
before every invocation. Invalid inputs do not initiate synthesis.

Concurrent calls coordinate compilation through a file lock. Verified artifacts
are reused across objects and Python processes using the same compatible runtime
installation. Cache keys include the contracts, scalar types, captured constants,
guidance, compiler/translator version, platform, and runtime installation.
Corrupted or incomplete entries are rebuilt.

## Inspect generated artifacts

After compiling, `function.artifact_dir` returns the directory containing the
verified source and native binary. Before compilation it returns `None`; merely
reading this property never starts synthesis.

```python
clamp.compile_sync()
print(clamp.artifact_dir)
```

The directory's `Verified<hash>.lean` file contains `pre` and `post` (the
translated specification), `implementation`, and `implementation_correct` (the
proof). The same directory also contains generated C, compiled artifacts, and
their integrity manifest. Treat these cache files as read-only.

The median example can print the source path directly:

```bash
AWS_PROFILE=my-bedrock-profile hatch run verified:python examples/verified_median.py --show-artifacts
```

## Supported contract types

- Function inputs and outputs can be `int`, `bool`, `float`, or `list[int]`.
  Integers retain arbitrary precision. Values must have the declared type;
  implicit integer/Boolean/float conversions are not performed.
- Validators are ordinary synchronous functions available in Python source
  files. Local assignments, `if`/`elif`/`else`, conditional expressions, early
  returns, assertions, and simple explicit failures are supported.
- Expressions support `+`, `-`, `*`, comparisons, and Boolean logic. Lists
  support equality, membership, concatenation, `len`, `sorted`, and slices
  without a step. A shallow snapshot of list references prevents caller
  mutation during validation or compilation from changing the verified input;
  the integer values are not copied.
- Bounded `all` and `any` generators over integer lists and slices are supported,
  including filters and nested quantifier expressions. Empty-domain and
  short-circuit behavior follow Python. General indexing, stepped slices,
  arbitrary calls, mutation, async validators, and AI validators are rejected.
- Captured numeric constants are frozen when the decorator is applied.
  Reapply the decorator to create a new specification after changing a captured
  constant. Mutable captured state is rejected.

Use constant result messages. Simple f-strings are supported on assertions;
integer/list interpolation in a `PostConditionResult` message is rejected
because it can raise under Python's integer-to-decimal conversion limit.

### Quantified search contracts

The [lower-bound example](../examples/verified_lower_bound.py) specifies a sorted
table lookup using properties of the returned position:

```python
def sorted_values(values: list[int]):
    assert values == sorted(values)


def insertion_position(result: int, values: list[int], key: int):
    assert 0 <= result <= len(values)
    assert all(value < key for value in values[:result])
    assert all(value >= key for value in values[result:])
```

For integer lists, equality with `sorted(values)` translates to the equivalent
pairwise ordering property. The returned position is specified independently of
the search algorithm. Functional verification does not establish logarithmic
complexity. List copying and runtime precondition validation also have a cost.

### Floating-point behavior

Python `float` maps to native IEEE-754 binary64 with a corresponding logical
model. Comparisons use IEEE equality: NaN is unequal to itself and positive and
negative zero compare equal. `math.isfinite`, `math.isnan`, and `math.isinf` are
supported in contracts. Use float literals such as `0.0` in float comparisons;
mixed integer/float comparisons are rejected rather than rounded silently.

Addition, subtraction, multiplication, and negation use binary64 semantics.
Native compilation disables multiply/add contraction to preserve separate
rounding steps. Division and transcendental functions in Python contracts are
not yet supported.

Finite values, infinities, subnormals, and signed zero cross the boundary without
decimal conversion. The pinned floating-point model/runtime canonicalizes NaNs;
preserving a NaN's payload or sign bits is not part of this interface. Contracts
cannot inspect raw floating-point bits.

Unsupported source is reported with the Python validator's name and location.
It is never dropped or approximated, including on an unreachable branch.
An assertion inside a postcondition stays a postcondition; it is not inferred
to be a precondition.

## Failures and configuration

`max_attempts` retains the existing convention: it counts retries after the
initial candidate. `max_attempts=3` permits four candidates; `0` permits one.
Verification failures are supplied to the model for the next candidate.

Setup errors and native build failures fail directly. Exhausted synthesis raises
the existing `AIFunctionError` base type, with a function-oriented message and
optional internal `diagnostics` for debugging. A failed candidate is never
installed or executed, and there is no fallback to an unverified implementation.

`compile_timeout` sets the timeout in seconds for each compiler/checker stage
(default 120). Cancellation terminates compiler subprocesses and releases cache
locks. `cache_dir` can select a different artifact cache, including for CI.

Verification establishes the written contracts. Their deterministic translation
and the native compiler/runtime are trusted implementation components. Keep the
contracts strong enough to specify the behavior the application needs.

Maintainers can find runtime build and release instructions in
[`runtime/README.md`](../runtime/README.md).
