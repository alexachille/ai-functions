"""Procedural memory type annotation and validation."""

import ast
import textwrap
from typing import Annotated

from pydantic import AfterValidator, Field

from ..tools.local_python_executor import SAFE_BUILTINS


class ProceduralMarker:
    """Tag that identifies Procedural-typed fields."""


def _get_import_modules(node: ast.Import | ast.ImportFrom) -> list[str]:
    """Extract the top-level module name(s) from an import node."""
    if isinstance(node, ast.ImportFrom):
        return [node.module.split(".")[0]] if node.module else []
    return [alias.name.split(".")[0] for alias in node.names]


def validate_procedural(value: str) -> str:
    """Validate that a procedural memory value contains valid Python with imports only from SAFE_BUILTINS."""
    if not value.strip():
        return value

    tree = ast.parse(value)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for module in _get_import_modules(node):
                if module not in SAFE_BUILTINS:
                    raise ValueError(
                        f"Import '{module}' is not allowed in procedural memory. "
                        f"Only imports from safe modules are permitted: {', '.join(sorted(SAFE_BUILTINS))}"
                    )

    return value


Procedural = Annotated[
    str,
    ProceduralMarker(),
    AfterValidator(validate_procedural),
    Field(
        default="# No code yet.",
        description=textwrap.dedent(
            """\
            Python functions that are always available in the Python execution environment of the agent

            Add to this parameter any function that could be useful for the agent to solve
            the task more quickly in the future. You can update this parameter to add additional
            utility functions even if the update does not directly relate to the feedback.

            Feedback to this parameter MUST include code snippets to add/modify or examples
            of inputs that should be supported or that currently fail.
            """
        ),
    ),
]
"""Type annotation to mark memory fields that should contain reusable Python functions.

Values are validated to ensure they contain syntactically valid Python with no
top-level imports (all imports must be inside function bodies).
"""
