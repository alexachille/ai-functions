from __future__ import annotations

import ast
import json
import textwrap
import uuid
from typing import Any

import yaml

_TRUNCATION_MARKER = " [...truncated...] "


def unique_name(name: str) -> str:
    """Append a 4-character random hex suffix to *name* (e.g. ``foo`` → ``foo_a1b2``)."""
    return f"{name}_{uuid.uuid4().hex[:4]}"


def bullet_points(values: list[str]) -> str:
    return "\n".join(f"- {v}" for v in values)


def truncate(value: Any, max_length: int = 500) -> str:
    """Truncate a value to at most *max_length* characters.

    Non-string values (dicts, lists) are JSON-serialized first.
    When truncation is needed the result keeps a prefix and suffix from the
    original text separated by a ``[...truncated...]`` marker, and the total
    length is guaranteed to be exactly *max_length*.
    """
    if isinstance(value, (dict, list)):
        value_str: str = json.dumps(value, ensure_ascii=False)
    else:
        value_str = str(value).strip()

    if len(value_str) <= max_length:
        return value_str

    # Ensure the output (prefix + marker + suffix) is exactly max_length
    available = max_length - len(_TRUNCATION_MARKER)
    if available <= 0:
        return value_str[:max_length]
    prefix_len = available // 2
    suffix_len = available - prefix_len
    return value_str[:prefix_len] + _TRUNCATION_MARKER + value_str[len(value_str) - suffix_len :]


def _str_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    """Represent strings using literal block style for multi-line values.

    Single-line values are emitted in plain (unquoted) style whenever possible.
    """
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data.rstrip("\n") + "\n", style="|")  # type: ignore[no-any-return]
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="")  # type: ignore[no-any-return]


class _LiteralDumper(yaml.Dumper):
    """Custom YAML dumper that avoids quoting / escaping string values."""

    # Prevent the emitter from analysing scalars and overriding our
    # chosen style with a quoted variant.
    def choose_scalar_style(self) -> str:
        if self.event.style == "|":  # type: ignore[union-attr]
            return "|"
        return super().choose_scalar_style()  # type: ignore[no-any-return]


_LiteralDumper.add_representer(str, _str_representer)


def to_yaml(obj: Any) -> str:
    """Convert *obj* to a human-readable YAML string.

    Multi-line strings are rendered as unquoted YAML literal blocks (``|``)
    without escapes, making code snippets and other multi-line content
    round-trip cleanly.
    """
    result: str = yaml.dump(
        obj,
        Dumper=_LiteralDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    return result.rstrip()


def extract_signatures(code: str) -> str:
    """Extract function signatures and their docstrings from Python source code.

    Given a string of Python code, returns a condensed version containing only
    the ``def`` lines (with full parameter lists) and the docstrings that
    immediately follow them.  Nested functions and methods inside classes are
    included.

    Args:
        code: A string containing valid Python source code.

    Returns:
        A string with one block per function: the ``def …:`` line followed by
        the indented docstring (if present), separated by blank lines.
        Returns the original *code* unchanged if it cannot be parsed.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    parts: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        if node.name.startswith("_"):
            continue

        # Reconstruct the def line from the AST
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        sig = f"{prefix} {node.name}({ast.unparse(node.args)}):"

        # Check for a return annotation
        if node.returns is not None:
            sig = f"{prefix} {node.name}({ast.unparse(node.args)}) -> {ast.unparse(node.returns)}:"

        docstring = ast.get_docstring(node)
        if docstring:
            indented = textwrap.indent(f'"""{docstring}"""', "    ")
            parts.append(f"{sig}\n{indented}")
        else:
            parts.append(f"{sig}\n    ...")

    return "\n\n".join(parts) if parts else code
