"""Generate a sorted-table insertion lookup from quantified Python contracts.

    AWS_PROFILE=my-profile hatch run verified:python examples/verified_lower_bound.py --show-artifacts

The contract states where the result belongs. It contains no search algorithm.
The native function is verified once for every sorted integer list and key.
"""

import argparse

from ai_functions import ai_verified_function
from ai_functions.ai_thread import AIFunctionError


def sorted_values(values: list[int]):
    assert values == sorted(values)


def insertion_position(result: int, values: list[int], key: int):
    assert 0 <= result <= len(values)
    assert all(value < key for value in values[:result])
    assert all(value >= key for value in values[result:])


@ai_verified_function(
    pre_conditions=[sorted_values],
    post_conditions=[insertion_position],
    max_attempts=5,
)
def lower_bound(values: list[int], key: int) -> int:
    """Return the first insertion index that preserves sorted order, including duplicates and missing keys."""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--show-artifacts", action="store_true", help="Print the generated specification, code, and proof"
    )
    arguments = parser.parse_args()
    try:
        lower_bound.compile_sync()
        for values, key in [([], 4), ([1, 3, 3, 8], 3), ([1, 3, 3, 8], 4), ([1, 3, 3, 8], 10), ([-9, -2, 7], -5)]:
            print(f"lower_bound({values}, {key}) = {lower_bound.run_sync(values, key)}")
        if arguments.show_artifacts and lower_bound.artifact_dir is not None:
            for path in sorted(lower_bound.artifact_dir.glob("*.lean")):
                print(f"Specification, implementation, and proof: {path}")
    except AIFunctionError as error:
        raise SystemExit(str(error)) from None
