"""Lean provisioning configuration and the resolved installation.

Not re-exported by the package. The cache directory holds managed toolchains, the
support executable (one build per toolchain and support-source hash), and the
per-project build directories under ``lean/projects/``.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DEFAULT_LEAN_TOOLCHAIN: str

__all__ = ["DEFAULT_LEAN_TOOLCHAIN", "LeanConfig", "ResolvedToolchain", "cache_path"]

@dataclass(frozen=True)
class LeanConfig:
    """How to obtain Lean, without performing setup on construction.

    Attributes:
        mode: ``auto`` prefers an exactly matching installed release, then the
            managed cache, and downloads only when necessary. ``system`` requires an
            installed release and never downloads. ``managed`` uses only the cache.
            Defaults to ``AI_FUNCTIONS_LEAN_TOOLCHAIN_MODE``, else ``auto``.
        cache_dir: Defaults to ``AI_FUNCTIONS_LEAN_CACHE_DIR``, else the platform's
            AI Functions cache.
    """

    mode: Literal["auto", "managed", "system"] = ...
    cache_dir: str | Path | None = ...
    def __post_init__(self) -> None:
        """Validate ``mode``.

        Raises:
            ValueError: ``mode`` is not one of the three modes.
        """
        ...

    def setup(
        self,
        lean_toolchain: str = DEFAULT_LEAN_TOOLCHAIN,
        *,
        offline: bool = False,
        timeout: float = 900.0,
    ) -> ResolvedToolchain:
        """Find or install exactly the pinned release.

        Args:
            lean_toolchain: The release pin, e.g. ``leanprover/lean4:v4.33.1``.
            offline: Forbid downloads.
            timeout: Bound on provisioning, cache lock waits included.

        Ensures:
            - The returned installation reports exactly the pinned version.
            - Global elan settings and the user's shell are unchanged.

        Raises:
            LeanSetupError: No matching installation can be obtained.
            LeanTimeoutError: ``timeout`` expired.

        Concurrency:
            Thread- and process-safe; concurrent installs of one pin serialize.
        """
        ...

def cache_path(config: LeanConfig) -> Path:
    """Return the absolute cache directory of ``config``, without creating it."""
    ...

@dataclass(frozen=True)
class ResolvedToolchain:
    """One concrete Lean installation.

    Native SDK and header checks run only when ``native_flags`` or
    ``native_identity`` is first read, so proof-only consumers need no host SDK.
    """

    root: Path
    identity: str
    environment_overrides: dict[str, str] = ...
    @property
    def lean(self) -> Path:
        """The ``lean`` executable of this installation, never an elan shim."""
        ...

    @property
    def lake(self) -> Path:
        """The ``lake`` executable of this installation."""
        ...

    @property
    def leanc(self) -> Path:
        """The native compiler driver of this installation."""
        ...

    @property
    def leanchecker(self) -> Path:
        """The kernel checker of this installation."""
        ...

    def environment(self, directory: Path | None = None) -> dict[str, str]:
        """Build a child-process environment isolated from the caller's Lean settings.

        Args:
            directory: Put on ``LEAN_PATH`` when given.

        Ensures:
            - Inherited ``LEAN*``, ``LAKE*``, ``DYLD_*`` and C search-path variables
              are removed.
            - This installation's ``bin`` leads ``PATH``.
        """
        ...

    @property
    def native_flags(self) -> tuple[str, ...]:
        """Host C compilation flags for native code; cached after the first read.

        Raises:
            LeanSetupError: A native prerequisite, such as ``lean.h``, is missing.
        """
        ...

    def link_args(self, *, python_extension: bool = False) -> list[str]:
        """Linker arguments for this runtime.

        Args:
            python_extension: Leave Python symbols unresolved, for an extension.
        """
        ...

    @property
    def native_identity(self) -> str:
        """Identity of the installation, host and flags native artifacts depend on."""
        ...
