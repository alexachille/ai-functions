"""A verified native function defined entirely through Python contracts.

Requires CPython 3.12+ and the verified installation extra. The first valid call
synthesizes and checks one implementation; subsequent calls reuse it.

The default model uses Amazon Bedrock. Select your AWS credentials with
AWS_PROFILE when running the example.
"""

from ai_functions import ai_verified_function
from ai_functions.ai_thread import AIFunctionError


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


@ai_verified_function(pre_conditions=[valid_bounds], post_conditions=[check_clamp], max_attempts=3)
def clamp(x: int, lo: int, hi: int) -> int:
    """Clamp x to the inclusive interval [lo, hi]."""


if __name__ == "__main__":
    try:
        clamp.compile_sync()
        print(clamp.run_sync(12, 0, 10))
        print(clamp.run_sync(-5, 0, 10))
    except AIFunctionError as error:
        raise SystemExit(str(error)) from None
