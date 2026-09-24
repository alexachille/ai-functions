"""Translate a decorated Python function directly to Lean text, typing it on the way.

Every lowered expression is a ``Term``: Lean text, its type, and whether Lean
decides it by instance search (only meaningful for ``Prop``). ``Prop`` and
``Bool`` are separate sorts: comparisons are propositions, ``decide`` turns a
decidable proposition into a ``Bool`` and ``= true`` turns a ``Bool`` into a
proposition. Each expression is lowered with the sort its context wants
(``PROP``, ``BOOL`` or ``None``), so a comparison is a proposition under
``assert`` and a ``Bool`` under ``if`` or where a value is needed.

Names in the body resolve when the decorator runs; a referenced ``Definition``
must already belong to the same project. Nothing here runs Lean.
"""

from __future__ import annotations

import ast
import builtins
import inspect
import textwrap
import typing
from collections.abc import Callable
from typing import Any, NamedTuple, NoReturn

from .definitions import Definition, Symbol, assume, every, implies
from .errors import TranslationError
from .types import BOOL, INT, PROP, Const, LeanType, render_type

INT_LIST = Const("List", (INT,))
_TYPES: dict[object, LeanType] = {int: INT, bool: BOOL, list[int]: INT_LIST}
# Python floors ``//`` and ``%``; Lean's ``Int`` ``/`` and ``%`` don't, so those
# rows name the floor variants. ``tests/test_verified_dsl.py`` checks every row.
_ARITH = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.FloorDiv: "Int.fdiv", ast.Mod: "Int.fmod"}
_COMPARE = {ast.Eq: "=", ast.NotEq: "≠", ast.Lt: "<", ast.LtE: "≤", ast.Gt: ">", ast.GtE: "≥"}
_SORTS = (PROP, BOOL)
# Lean tokens a Python identifier can spell; quoted as names.
_KEYWORDS = frozenset(
    """_ at by do end fun have let match then with show from where this suffices obtain calc forall exists
    deriving instance def theorem lemma example abbrev axiom opaque open namespace section variable universe
    import export structure class inductive mutual private protected noncomputable partial unsafe macro syntax
    notation infix infixl infixr prefix postfix local scoped attribute omit include extends termination_by
    decreasing_by nomatch nofun mut unless return try catch finally Type Sort Prop sorry admit""".split()
)
# Globals the emitted text spells; a binder spelled like one is renamed with ``'``.
_GLOBALS = frozenset({"Int", "Nat", "List", "Bool", "Prop", "Unit", "True", "decide", "true", "false"})


class Term(NamedTuple):
    """Lean text with its type; ``decidable`` matters only for a ``Prop``."""

    text: str
    type: LeanType
    decidable: bool = True


def _quote(part: str) -> str:
    return f"«{part}»" if part in _KEYWORDS else part


def site(fn: Callable[..., Any]) -> str:
    """Return ``file:line`` of a function's definition."""
    code = getattr(fn, "__code__", None)
    return f"{code.co_filename}:{code.co_firstlineno}" if code else "<unknown>"


def _signature(fn: Callable[..., Any], prop: bool) -> tuple[tuple[tuple[str, LeanType], ...], LeanType]:
    """Return the Lean parameters and result type; a proposition's ``bool`` result is ``Prop``."""
    try:
        hints = typing.get_type_hints(fn)
        parameters = []
        for p in inspect.signature(fn).parameters.values():
            if p.kind not in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) or p.default is not p.empty:
                raise TranslationError("DSL parameters must be positional, without defaults")
            if hints.get(p.name) not in _TYPES:
                raise TranslationError(f"parameter {p.name} needs an int, bool or list[int] annotation")
            parameters.append((p.name, _TYPES[hints[p.name]]))
        result = _TYPES.get(hints.get("return"))
        if result is None or (prop and result != BOOL):
            raise TranslationError("the result needs a bool annotation" if prop else "the result needs an annotation")
    except (TranslationError, NameError, TypeError) as exc:
        raise TranslationError(f"{site(fn)}: {fn.__qualname__}: {exc}") from exc
    return tuple(parameters), PROP if prop else result


def adapt(symbol: Symbol, fn: Callable[..., Any], *, prop: bool, runtime: bool) -> Definition:
    """Return a ``Definition`` giving an existing Lean declaration a Python signature."""
    parameters, result = _signature(fn, prop)
    return Definition(fn, symbol, "", parameters, result, runtime=runtime)


def translate(fn: Callable[..., Any], symbol: Symbol, *, prop: bool) -> Definition:
    """Translate ``fn`` to a ``def`` named ``symbol.name``; it may call definitions of ``symbol.project``."""
    parameters, result = _signature(fn, prop)
    try:
        lines, first = inspect.getsourcelines(fn)
        node = next(n for n in ast.parse(textwrap.dedent("".join(lines))).body if isinstance(n, ast.FunctionDef))
    except (OSError, TypeError, SyntaxError, StopIteration) as exc:
        raise TranslationError(f"{site(fn)}: define {fn.__qualname__} with def in a Python source file") from exc
    name = symbol.name
    lowerer = _Lowerer(symbol.project, fn, first)
    env: dict[str, Term] = {}
    for parameter, ty in parameters:
        env = lowerer.bind(parameter, ty, env)[1]
    body = lowerer.body(node, env, result)
    header = " ".join(
        ["def", ".".join(map(_quote, name.split("."))), *(f"({t.text} : {render_type(t.type)})" for t in env.values())]
    )
    return Definition(fn, symbol, f"{header} : {render_type(result)} :=\n  {body.text}", parameters, result)


class _Lowerer:
    def __init__(self, project: object, fn: Callable[..., Any], first_line: int):
        self.project, self.first_line = project, first_line
        code = fn.__code__
        self.filename = code.co_filename
        nonlocals = {}
        for name, cell in zip(code.co_freevars, fn.__closure__ or (), strict=True):
            try:
                nonlocals[name] = cell.cell_contents
            except ValueError:
                pass  # Assigned later in the enclosing function: not bound yet.
        self.globals = {**vars(builtins), **fn.__globals__, **nonlocals}
        for name in (*code.co_varnames, *code.co_cellvars):
            self.globals.pop(name, None)

    def fail(self, node: ast.AST, message: str) -> NoReturn:
        raise TranslationError(f"{self.filename}:{self.first_line + getattr(node, 'lineno', 1) - 1}: {message}")

    def lookup(self, node: ast.AST) -> object:
        if not isinstance(node, ast.Name):
            self.fail(node, "Use a named decorated definition or supported builtin")
        if node.id not in self.globals:
            self.fail(node, f"{node.id} is not bound; decorate helpers above their use")
        return self.globals[node.id]

    def bind(self, hint: str, ty: LeanType, env: dict[str, Term]) -> tuple[Term, dict[str, Term]]:
        """Bind a Python name, renaming it apart from printed globals and binders in scope."""
        taken, name = {t.text for t in env.values()}, hint
        while name in _GLOBALS or _quote(name) in taken:
            name += "'"
        var = Term(_quote(name), ty, False)
        return var, {**env, hint: var}

    def coerce(self, node: ast.AST, t: Term, sort: LeanType) -> Term:
        if t.type == sort:
            return t
        if t.type == PROP and sort == BOOL:
            if not t.decidable:
                self.fail(
                    node, "Only a decidable proposition (comparisons, connectives, bounded quantifiers) is a Bool"
                )
            return Term(f"(decide {t.text})", BOOL)
        if t.type == BOOL and sort == PROP:
            return Term(f"({t.text} = true)", PROP)
        self.fail(node, f"Expected {render_type(sort)}, got {render_type(t.type)}")

    def logic(self, node: ast.AST, op: str, left: Term, right: Term) -> Term:
        sort = left.type
        if sort not in _SORTS or right.type != sort or (op == "implies" and sort != PROP):
            self.fail(node, "Boolean operators require operands of one sort, Bool or Prop")
        symbol = {"and": "∧" if sort == PROP else "&&", "or": "∨" if sort == PROP else "||", "implies": "→"}[op]
        return Term(f"({left.text} {symbol} {right.text})", sort, left.decidable and right.decidable)

    def ints(self, node: ast.AST, *terms: Term, what: str = "Arithmetic") -> None:
        if any(t.type != INT for t in terms):
            self.fail(node, f"{what} requires Int operands, got {', '.join(render_type(t.type) for t in terms)}")

    def quantifier(self, node: ast.AST, kind: str, var: Term, domain: Term | None, body: Term) -> Term:
        if body.type not in _SORTS or (domain is None and body.type != PROP):
            self.fail(node, "A quantified body requires Bool, or Prop when unbounded")
        if body.type == BOOL:
            return Term(f"(List.{kind} {domain.text} (fun {var.text} => {body.text}))", BOOL)
        quantifier, connective = ("∀", "→") if kind == "all" else ("∃", "∧")
        head = f"{quantifier} ({var.text} : {render_type(var.type)})"
        if domain is None:
            return Term(f"({head}, {body.text})", PROP, False)
        return Term(f"({head}, {var.text} ∈ {domain.text} {connective} {body.text})", PROP, body.decidable)

    def expression(self, node: ast.expr, env: dict[str, Term], want: LeanType | None = None) -> Term:
        """Lower ``node`` in a context that wants sort ``want`` (``PROP``, ``BOOL`` or ``None``)."""
        t = self._expression(node, env, want)
        return t if want is None else self.coerce(node, t, want)

    def value(self, node: ast.expr, env: dict[str, Term]) -> Term:
        """Lower a value position: a decidable proposition becomes its ``Bool``, as in Python."""
        t = self.expression(node, env)
        return self.coerce(node, t, BOOL) if t.type == PROP and t.decidable else t

    def constant(self, node: ast.AST, value: object) -> Term:
        if type(value) is bool:
            return Term("true" if value else "false", BOOL)
        if type(value) is int:
            return Term(f"({value} : Int)", INT)
        self.fail(node, "DSL constants must be int or bool")

    def apply(self, node: ast.AST, definition: Definition, args: list[ast.expr], env: dict[str, Term]) -> Term:
        if definition.project is not self.project:
            self.fail(node, f"{definition.__qualname__} is a definition of another project")
        if len(args) != len(definition.parameters):
            self.fail(node, "Apply a decorated definition to all of its declared parameters")
        # Rooted, so no binder or enclosing namespace can capture the reference.
        name = "_root_." + ".".join(map(_quote, definition.symbol.name.split(".")))
        texts = [name]
        for arg, (_, ty) in zip(args, definition.parameters, strict=True):
            t = self.expression(arg, env, ty if ty in _SORTS else None)
            if t.type != ty:
                self.fail(arg, f"Expected {render_type(ty)}, got {render_type(t.type)}")
            texts.append(t.text)
        return Term(f"({' '.join(texts)})" if args else name, definition._result, False)

    def _expression(self, node: ast.expr, env: dict[str, Term], want: LeanType | None) -> Term:
        if isinstance(node, ast.Constant):
            return self.constant(node, node.value)
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            value = self.lookup(node)
            return self.apply(node, value, [], env) if isinstance(value, Definition) else self.constant(node, value)
        if isinstance(node, ast.BinOp) and type(node.op) in _ARITH:
            left, right, op = self.expression(node.left, env), self.expression(node.right, env), _ARITH[type(node.op)]
            self.ints(node, left, right)
            return Term(
                f"({op} {left.text} {right.text})" if op[0].isalpha() else f"({left.text} {op} {right.text})", INT
            )
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                t = self.expression(node.operand, env, want)
                if t.type not in _SORTS:
                    self.fail(node, "not requires Bool or Prop")
                return Term(f"({'¬' if t.type == PROP else '!'}{t.text})", t.type, t.decidable)
            t = self.expression(node.operand, env)
            self.ints(node, t)
            if isinstance(node.op, ast.USub):
                return Term(f"(-{t.text})", INT)
            if isinstance(node.op, ast.UAdd):
                return t
        if isinstance(node, ast.Compare):
            left, result = self.value(node.left, env), None
            for operation, right_node in zip(node.ops, node.comparators, strict=True):
                op, right = _COMPARE.get(type(operation)), self.value(right_node, env)
                if op is None or left.type != right.type:
                    self.fail(node, f"Unsupported comparison of {render_type(left.type)} and {render_type(right.type)}")
                if op in ("<", "≤", ">", "≥"):
                    self.ints(node, left, right, what=op)
                elif left.type not in (INT, BOOL, INT_LIST):
                    self.fail(node, "Equality requires int, bool, or list[int]")
                compared = self.coerce(node, Term(f"({left.text} {op} {right.text})", PROP), want or PROP)
                result = compared if result is None else self.logic(node, "and", result, compared)
                left = right
            return result  # type: ignore[return-value]
        if isinstance(node, ast.BoolOp):
            values = [self.expression(v, env, want) for v in node.values]
            sort = want or (PROP if any(v.type == PROP for v in values) else BOOL)
            values = [self.coerce(node, v, sort) for v in values]
            result = values[0]
            for v in values[1:]:
                result = self.logic(node, "and" if isinstance(node.op, ast.And) else "or", result, v)
            return result
        if isinstance(node, ast.IfExp):
            condition = self.expression(node.test, env, BOOL)
            then, orelse = self.expression(node.body, env, want), self.expression(node.orelse, env, want)
            if want is None and {then.type, orelse.type} == {PROP, BOOL}:
                then, orelse = self.coerce(node, then, BOOL), self.coerce(node, orelse, BOOL)
            if then.type != orelse.type:
                self.fail(node, "Conditional expressions require matching branch types")
            return Term(f"(if {condition.text} then {then.text} else {orelse.text})", then.type, False)
        if isinstance(node, ast.List):
            items = [self.expression(v, env) for v in node.elts]
            self.ints(node, *items, what="A list literal")
            return Term(f"([{', '.join(t.text for t in items)}] : List Int)", INT_LIST)
        if isinstance(node, ast.Call):
            return self.call(node, env, want)
        self.fail(node, f"Unsupported expression: {type(node).__name__}")

    def call(self, node: ast.Call, env: dict[str, Term], want: LeanType | None) -> Term:
        if node.keywords or (isinstance(node.func, ast.Name) and node.func.id in env):
            self.fail(node, "Calls take positional arguments and target decorated definitions or builtins")
        target, args = self.lookup(node.func), node.args
        if isinstance(target, Definition):
            return self.apply(node, target, args, env)
        if target in (all, any) and len(args) == 1 and isinstance(args[0], ast.GeneratorExp):
            return self.quantified("all" if target is all else "any", args[0], env, want)
        if target is implies and len(args) == 2:
            left, right = (self.expression(a, env, PROP) for a in args)
            return self.logic(node, "implies", left, right)
        if target in (len, sum) and len(args) == 1:
            t = self.expression(args[0], env)
            if t.type != INT_LIST:
                self.fail(node, "len and sum require list[int]")
            return Term(f"(Int.ofNat (List.length {t.text}))" if target is len else f"(List.sum {t.text})", INT)
        self.fail(node, "Calls must target decorated definitions or supported builtins")

    def quantified(self, kind: str, generator: ast.GeneratorExp, env: dict[str, Term], want: LeanType | None) -> Term:
        if len(generator.generators) != 1:
            self.fail(generator, "Only one generator clause is supported")
        loop = generator.generators[0]
        domain, var, scope = self.domain(loop.target, loop.iter, env)
        sort = want or (PROP if domain is None else BOOL)
        if domain is None and sort != PROP:
            self.fail(generator, "Unbounded quantifiers require @project.proposition")
        body = self.expression(generator.elt, scope, sort)
        for guard in reversed(loop.ifs):
            condition = self.expression(guard, scope, sort)
            if sort == BOOL:
                body = Term(f"(if {condition.text} then {body.text} else {str(kind == 'all').lower()})", BOOL)
            else:
                body = self.logic(guard, "implies" if kind == "all" else "and", condition, body)
        return self.quantifier(generator, kind, var, domain, body)

    def domain(self, target: ast.expr, iterable: Any, env: dict[str, Term]):
        if not isinstance(target, ast.Name):
            self.fail(target, "Quantifiers require one named variable")
        callee = self.lookup(iterable.func) if isinstance(iterable, ast.Call) else None
        domain: Term | None
        if callee is every:
            if len(iterable.args) != 1 or iterable.keywords:
                self.fail(iterable, "every requires one type")
            domain, ty = None, self.annotation(iterable.args[0])
        elif callee is range:
            bounds = [self.expression(a, env) for a in iterable.args]
            if len(bounds) not in (1, 2) or iterable.keywords:
                self.fail(iterable, "range requires one or two integer bounds")
            self.ints(iterable, *bounds, what="range")
            start, stop = ("(0 : Int)", bounds[0].text) if len(bounds) == 1 else (bounds[0].text, bounds[1].text)
            # ``·`` is a hygienic binder: nothing in ``start`` can be captured.
            text = f"(List.map ({start} + Int.ofNat ·) (List.range (Int.toNat ({stop} - {start}))))"
            domain, ty = Term(text, INT_LIST), INT
        else:
            domain = self.expression(iterable, env)
            if not (isinstance(domain.type, Const) and domain.type.name == "List"):
                self.fail(iterable, "Quantifier domains must be lists, range, or every")
            ty = domain.type.args[0]
        var, scope = self.bind(target.id, ty, env)
        return domain, var, scope

    def annotation(self, node: ast.expr) -> LeanType:
        spelled = {"int": INT, "bool": BOOL, "list[int]": INT_LIST}.get(ast.unparse(node))
        return spelled or self.fail(node, "Annotations must be int, bool or list[int]")

    def body(self, node: ast.FunctionDef, env: dict[str, Term], result: LeanType) -> Term:
        statements = node.body[1:] if ast.get_docstring(node) is not None else node.body
        if len(statements) == 1 and isinstance(statements[0], ast.Return) and statements[0].value is not None:
            t = self.expression(statements[0].value, env, result if result in _SORTS else None)
        elif result != PROP:
            self.fail(node, "@project.function requires a body of one return statement")
        else:
            t = self.block(statements, env)
        if t.type != result:
            self.fail(node, "Return expression does not match the declared result type")
        return t

    def scoped(self, node: ast.stmt, rest: list[ast.stmt]) -> None:
        assigned = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        loads = (n for s in rest for n in ast.walk(s) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load))
        if any(n.id in assigned for n in loads):
            self.fail(node, "Branch/loop-local assignments cannot be read after their scope")

    def block(self, statements: list[ast.stmt], env: dict[str, Term], nested: bool = False) -> Term:
        """Lower a statement list; ``nested`` is set inside a ``for`` body or an ``if``/``else`` branch."""
        if not statements:
            return Term("True", PROP)
        node, *rest = statements

        def then(p: Term) -> Term:
            return self.logic(node, "and", p, self.block(rest, env, nested)) if rest else p

        if isinstance(node, ast.Assert):
            return then(self.expression(node.test, env, PROP))
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if self.lookup(call.func) is assume and len(call.args) == 1 and not call.keywords:
                # A false ``assume`` ends the Python call: Lean's implication only at the top level.
                if nested:
                    self.fail(node, "assume is only allowed at the top level; in a loop or branch use `if p: rest`")
                hypothesis = self.expression(call.args[0], env, PROP)
                return self.logic(node, "implies", hypothesis, self.block(rest, env))
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if len(targets) != 1 or not isinstance(targets[0], ast.Name) or node.value is None:
                self.fail(node, "Assign one local variable")
            if isinstance(node, ast.AnnAssign):
                annotation = self.annotation(node.annotation)
                value = self.expression(node.value, env, annotation if annotation in _SORTS else None)
                if value.type != annotation:
                    self.fail(node, "Local annotation does not match the assigned value")
            else:
                value = self.value(node.value, env)
            var, scope = self.bind(targets[0].id, value.type, env)
            body = self.block(rest, scope, nested)
            return Term(f"(let {var.text} : {render_type(var.type)} := {value.text}; {body.text})", body.type, False)
        if isinstance(node, ast.If):
            condition = self.expression(node.test, env, PROP)
            branches = self.logic(node, "implies", condition, self.block(node.body, env, True))
            if node.orelse:
                negated = Term(f"(¬{condition.text})", PROP, condition.decidable)
                otherwise = self.logic(node, "implies", negated, self.block(node.orelse, env, True))
                branches = self.logic(node, "and", branches, otherwise)
            self.scoped(node, rest)
            return then(branches)
        if isinstance(node, ast.For):
            if node.orelse:
                self.fail(node, "for/else is not supported")
            self.scoped(node, rest)
            domain, var, scope = self.domain(node.target, node.iter, env)
            return then(self.quantifier(node, "all", var, domain, self.block(node.body, scope, True)))
        if isinstance(node, ast.Pass):
            return self.block(rest, env, nested)
        self.fail(node, f"Unsupported proposition statement: {type(node).__name__}")
