"""Warm Lean elaboration over a prepared project; certificates are checked by ``checker``."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import TracebackType

from ._support import SupportSession, build_support_executable
from .errors import LeanError
from .project import LeanProject, _render_name
from .toolchain import cache_path


@dataclass(frozen=True)
class Source:
    """Lean text and the UTF-8 byte ranges ``[start, stop)`` of it the model wrote."""

    text: str = ""
    ranges: tuple[tuple[int, int], ...] = ()

    @classmethod
    def model(cls, text: str) -> Source:
        """Return ``text`` with all of it marked as model text."""
        return cls(text, ((0, len(text.encode())),) if text else ())

    @staticmethod
    def of(source: str | Source) -> Source:
        """Return harness text as a ``Source``, and a ``Source`` unchanged."""
        return source if isinstance(source, Source) else Source(source)

    @staticmethod
    def join(separator: str, parts: Iterable[str | Source]) -> Source:
        """Concatenate ``parts`` with harness text ``separator`` between them."""
        joined = Source()
        for index, part in enumerate(parts):
            joined = joined + (separator if index else "") + part
        return joined

    def __add__(self, other: str | Source) -> Source:
        """Concatenate, keeping both sides' model ranges."""
        other = Source.of(other)
        shift = len(self.text.encode())
        return Source(self.text + other.text, self.ranges + tuple((a + shift, b + shift) for a, b in other.ranges))

    def __radd__(self, other: str) -> Source:
        """Prefix harness text."""
        return Source(other) + self


@dataclass(frozen=True)
class Message:
    """One Lean message; ``offset`` is its UTF-8 byte offset, comparable with ``Source.ranges``."""

    severity: str
    text: str
    line: int | None = None
    col: int | None = None
    offset: int | None = None


@dataclass
class ElabResult:
    """The outcome of one elaboration request."""

    ok: bool
    errors: str = ""
    stdout: str = ""
    declared: tuple[str, ...] = ()
    axioms: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    messages: tuple[Message, ...] = ()
    raw: Mapping[str, object] = field(default_factory=dict)


def _result(reply: dict) -> ElabResult:
    messages = tuple(
        Message(m["severity"], m["text"], m.get("line"), m.get("col"), m.get("offset")) for m in reply["messages"]
    )
    return ElabResult(
        ok=reply["ok"] is True,
        errors="\n".join(
            (f"{m.line}:{m.col}: " if m.line is not None else "") + m.text for m in messages if m.severity == "error"
        ),
        stdout="\n".join([*(m.text for m in messages if m.severity == "info"), reply.get("stdout", "")]),
        declared=tuple(_render_name(parts) for parts in reply.get("declared", ())),
        axioms={name: tuple(found) for name, found in reply.get("axioms", {}).items()},
        messages=messages,
        raw=reply,
    )


class LeanServer:
    """One Lean process holding an elaboration state over a prepared project; not thread-safe.

    The process's committed state is the only state. It starts on the first
    request; a timeout or its death raises and closes the server for good.
    """

    def __init__(
        self,
        project: LeanProject,
        imports: Sequence[str] = (),
        *,
        header: Sequence[str] = (),
        timeout: float = 120.0,
    ) -> None:
        self._project = project
        self._extra_imports = tuple(imports)
        self._header = tuple(header)
        self._timeout = timeout
        self._session: SupportSession | None = None
        self._closed = False

    @property
    def imports(self) -> tuple[str, ...]:
        """The modules the process imports, and a rendered ledger must import; prepares the project."""
        return tuple(dict.fromkeys((*self._project.prepare().imports, *self._extra_imports)))

    def _request(self, payload: dict) -> dict:
        if self._closed:
            raise LeanError("The Lean server is closed")
        try:
            if self._session is None:
                prepared = self._project.prepare()
                executable = build_support_executable(
                    tools=prepared.lake.tools, cache=cache_path(self._project.toolchain), timeout=self._timeout
                )
                self._session = SupportSession(prepared.lake, executable)
                from .checker import MODULE  # The cold compile's module, so private names agree.

                source = "\n\n".join((prepared.prelude, *self._header))
                init = {"op": "init", "imports": list(self.imports), "source": source, "module": MODULE}
                init = self._session.request(init, timeout=self._timeout)
                if init.get("ok") is not True:
                    raise LeanError(f"Lean server initialization failed:\n{_result(init).errors or init}")
            return self._session.request(payload, timeout=self._timeout)
        except BaseException:
            self.close()
            raise

    def _elab(self, op: str, source: str | Source, **fields: object) -> ElabResult:
        source = Source.of(source)
        return _result(self._request({"op": op, "source": source.text, "model": source.ranges, **fields}))

    def commit(self, source: str | Source) -> ElabResult:
        """Elaborate a block against the committed state and keep it if it succeeds."""
        return self._elab("elab", source, commit=True)

    def probe(self, source: str | Source) -> ElabResult:
        """Elaborate against the committed state without keeping anything."""
        return self._elab("elab", source, commit=False)

    def parse_term(self, source: str) -> ElabResult:
        """Parse ``source`` as exactly one term, reporting ``raw["leading_by"]`` and ``raw["ended"]``."""
        reply = self._request({"op": "parse_term", "source": source})
        return _result({"messages": [], **reply})

    def evaluate(self, source: str | Source, names: Sequence[str]) -> tuple[ElabResult, list[object]]:
        """Probe ``source`` and reduce each named constant to closed-term JSON by ``whnf``."""
        result = self._elab("eval", source, names=list(names))
        return result, list(result.raw.get("values", []))

    def closed_term(self, source: str | Source, name: str) -> tuple[ElabResult, str | None]:
        """Probe ``source``; return ``name``'s value as closed text over the project, or ``None`` on failure."""
        result = self._elab("closed", source, name=name)
        term = result.raw.get("term")
        return result, term if result.ok and isinstance(term, str) else None

    def collect_axioms(self, name: str, *, extra: str | Source = "") -> tuple[ElabResult, tuple[str, ...] | None]:
        """Probe ``extra``; return ``name``'s axioms, or ``None`` on failure."""
        result = self._elab("elab", extra, commit=False, axioms=[name])
        return result, result.axioms.get(name) if result.ok else None

    def close(self) -> None:
        """Stop the process; idempotent."""
        self._closed = True
        if self._session is not None:
            self._session.close()

    def __enter__(self) -> LeanServer:
        """Enter the server's lifetime."""
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        """Close the server."""
        self.close()
