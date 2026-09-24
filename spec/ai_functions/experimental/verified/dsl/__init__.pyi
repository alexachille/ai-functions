"""The Python contract language, independent of any verification backend.

``assume``, ``every`` and ``implies`` are the DSL helpers; ``Definition`` is what a
project's decorators return. The translation to Lean text is private to this package
and imports nothing from ``verified.lean``, ``verified.function`` or ``verified.compile``.
"""

from .definitions import Definition, assume, every, implies

__all__ = ["Definition", "assume", "every", "implies"]
