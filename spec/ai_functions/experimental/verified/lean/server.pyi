"""Warm Lean elaboration over a prepared project.

One support executable serves this server and the checker; it is built once per
toolchain into the shared cache and runs in the project's Lake environment.
Requests and replies are one JSON object per line.

Confinement rules, applied by Lean to every range a ``Source`` marks as model text:

- A command starting in model text is ``def``, ``theorem``, ``abbrev``,
  ``example``, ``structure``, ``inductive``, ``open``, ``variable``, ``universe``,
  or ``… in …`` of these; in a probe, also ``#check`` or ``#print``.
- Model text contains no ``unsafe``, ``partial`` or ``meta`` modifier, ``unsafe``
  term, ``by_elab``, ``run_tac``, ``include_str``, ``sorry``, ``admit``,
  ``native_decide``, ``set_option``, or ``_root_`` declaration name.
- Every attribute in model text is ``simp``, ``reducible``, ``irreducible`` or
  ``inline``.
- No declaration added by a command with model text is ``unsafe`` or refers to an
  ``unsafe`` constant.

A rejected command does not elaborate and nothing after it runs.

Invariants:
    V1, V2.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import TracebackType

from .project import LeanProject

@dataclass(frozen=True)
class Source:
    """Lean text and the UTF-8 byte ranges ``[start, stop)`` of it the model wrote.

    A plain ``str`` is harness text. Concatenation keeps each side's ranges where
    its text lands.
    """

    text: str = ""
    ranges: tuple[tuple[int, int], ...] = ()

    @classmethod
    def model(cls, text: str) -> Source:
        """Return ``text`` with all of it marked as model text."""
        ...

    @staticmethod
    def of(source: str | Source) -> Source:
        """Return harness text as a ``Source``, and a ``Source`` unchanged."""
        ...

    @staticmethod
    def join(separator: str, parts: Iterable[str | Source]) -> Source:
        """Concatenate ``parts`` with harness text ``separator`` between them."""
        ...

    def __add__(self, other: str | Source) -> Source: ...
    def __radd__(self, other: str) -> Source: ...

@dataclass(frozen=True)
class Message:
    """One Lean message and its position in the request's text.

    Attributes:
        line: 1-based, as Lean prints it; ``None`` when Lean gave no position.
        col: Codepoints from 0, as Lean prints it.
        offset: UTF-8 byte offset, comparable with ``Source.ranges``.
    """

    severity: str
    text: str
    line: int | None = None
    col: int | None = None
    offset: int | None = None

@dataclass
class ElabResult:
    """The outcome of one elaboration request.

    Attributes:
        ok: Lean reported no error and every requested inventory was collected;
            says nothing about an axiom policy.
        errors: Diagnostics suitable as model feedback.
        stdout: Printed output, e.g. from ``#check``.
        declared: Added constants with a source range, not nested under another.
        axioms: Requested names mapped to their transitive axiom inventories.
        messages: Every Lean message, with its position.
        raw: The full reply.
    """

    ok: bool
    errors: str = ""
    stdout: str = ""
    declared: tuple[str, ...] = ()
    axioms: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    messages: tuple[Message, ...] = ()
    raw: Mapping[str, object] = field(default_factory=dict)

class LeanServer:
    """One Lean process holding an elaboration state over a prepared project.

    The process's committed state is the only state; Python keeps no copy of the
    committed blocks. The caller that commits (the proof session) keeps its own
    records and renders the ledger from them. The first request prepares the
    project and starts the process with the imports and header (the prelude, then
    ``header``).

    Lifecycle:
        IDLE → RUNNING on the first request → CLOSED on ``close``. If the process
        dies, the request raises ``LeanError`` and the server is CLOSED; it is
        never restarted or replayed.
    """

    def __init__(
        self,
        project: LeanProject,
        imports: Sequence[str] = (),
        *,
        header: Sequence[str] = (),
        timeout: float = 120.0,
    ) -> None:
        """Configure a server; starts no process.

        Args:
            project: The project to elaborate against.
            imports: Extra modules imported after the project's.
            header: Harness commands elaborated after the prelude.
            timeout: Seconds per request; the process is killed on expiry.
        """
        ...

    @property
    def imports(self) -> tuple[str, ...]:
        """The modules the process imports, and a rendered ledger must import; prepares the project."""
        ...

    def commit(self, source: str | Source) -> ElabResult:
        """Elaborate a block against the committed state and keep it if it succeeds.

        Ensures:
            - On success, the block extends the committed state.
            - On failure, the committed state is unchanged.

        Raises:
            LeanTimeoutError: The request exceeded ``timeout``; the server is CLOSED.
            LeanError: The process died; the server is CLOSED.
        """
        ...

    def probe(self, source: str | Source) -> ElabResult:
        """Elaborate against the committed state without keeping anything."""
        ...

    def parse_term(self, source: str) -> ElabResult:
        """Parse ``source`` as exactly one term, without elaborating it.

        Ensures:
            - ``raw["leading_by"]`` is whether the first token is ``by``.
            - On failure, ``raw["ended"]`` is the ``{"line", "col"}`` where a
              complete term ended before the input did, else ``None``.
        """
        ...

    def evaluate(self, source: str | Source, names: Sequence[str]) -> tuple[ElabResult, list[object]]:
        """Probe ``source`` and reduce each named constant to closed-term JSON.

        Returns:
            The probe's result and one value per name, in order.

        Ensures:
            - Reduction is ``whnf`` at default transparency; no compiled code runs.
            - A value is ``None`` when reduction is stuck (e.g. on an opaque) or
              leaves the boundary fragment.
            - ``result.raw["types"]`` holds each name's type as
              ``{"type", "type_str"}``.
        """
        ...

    def closed_term(self, source: str | Source, name: str) -> tuple[ElabResult, str | None]:
        """Probe ``source``; return the value of constant ``name`` as closed text over the project.

        Ensures:
            - The constants declared after initialization (the ledger's ``H``, ``A``
              and ``J``) are unfolded; project and core constants are kept, printed
              with full names.
            - Fails closed: the text is returned only if it, and the printed type,
              re-elaborate over the initialized state alone (the imports and the
              header), confined as model text, to a kernel-defeq value and type.
              Otherwise the text is ``None`` and ``result`` is failed with an error, e.g.
              when the value depends on a ``J`` axiom.
        """
        ...

    def collect_axioms(self, name: str, *, extra: str | Source = "") -> tuple[ElabResult, tuple[str, ...] | None]:
        """Probe ``extra``; return ``name``'s axioms, or ``None`` on failure."""
        ...

    def close(self) -> None:
        """Stop the process; idempotent. The server is CLOSED."""
        ...

    def __enter__(self) -> LeanServer: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the server."""
        ...
