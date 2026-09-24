"""Errors of the Python contract language."""


class TranslationError(RuntimeError):
    """A Python definition is outside the DSL; the message carries its ``file:line``."""


class NotComputable(RuntimeError):
    """A DSL definition was evaluated in Python but has no Python meaning."""
