"""Template utilities for AI Functions.

This module provides template string handling with support for Python 3.10-3.14+.
On Python 3.14+, uses native t-string types from string.templatelib.
On earlier versions, provides a compatible backport implementation.
"""

import textwrap
from typing import Any

from tstr import Interpolation, Template


def render_template_with_indent(template: Template) -> str:
    """Render a template while preserving indentation for interpolated values.

    Dedents the template, then renders values while maintaining the indentation
    level of their placeholder.

    Args:
        template: A Template object containing strings and Interpolations

    Returns:
        The rendered string with proper indentation
    """
    # Build temp string with placeholders, collect values
    parts: list[str] = []
    values: list[Any] = []
    for item in template:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, Interpolation):
            parts.append(f"__INTERP_{len(values)}__")
            values.append(item.value)

    temp_str = "".join(parts)
    result = textwrap.dedent(temp_str).strip("\n")
    lines = result.split("\n")

    # Replace each interpolation placeholder while preserving indentation
    for idx, value in enumerate(values):
        placeholder = f"__INTERP_{idx}__"
        str_value = str(value)

        for i, line in enumerate(lines):
            indent_len = _count_leading_spaces_to_match(line, placeholder)
            if indent_len is None:
                continue
            elif indent_len == 0:
                lines[i] = line.replace(placeholder, str_value)
            else:
                indented_value = textwrap.indent(str_value, " " * indent_len)
                lines[i] = line[indent_len:].replace(placeholder, indented_value)

    return "\n".join(lines)


def _count_leading_spaces_to_match(string: str, substring: str) -> int | None:
    """Count leading spaces before a substring match.

    If there are only spaces from the start of the string to the match,
    returns the count of those spaces. Otherwise returns 0.

    Args:
        string: The string to search in
        substring: The substring to find

    Returns:
        Number of leading spaces, 0 if non-space chars precede match, None if not found
    """
    index = string.find(substring)
    if index == -1:
        return None

    before_match = string[:index]
    if before_match == " " * len(before_match):
        return len(before_match)
    else:
        return 0
