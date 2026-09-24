"""Certification failures."""


class ContractNotProved(AssertionError):
    """No accepted certificate proves the returned value."""

    def __init__(self, message: str, diagnostics: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.diagnostics = tuple(diagnostics)
