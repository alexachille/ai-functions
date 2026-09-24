"""Compile against the raw-Lean contracts in `fixtures/verified/contracts`, with a scripted model."""

import textwrap
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from ai_functions.experimental.verified.compile import ai_compile
from ai_functions.experimental.verified.lean import LeanProject
from ai_functions.testing import ScriptedModel, Turn

FIXTURE = Path(__file__).parent / "fixtures" / "verified" / "contracts"


@dataclass(frozen=True)
class Contract:
    """A contract in `Contracts.lean`: its namespace, and the binders and type of the function it constrains."""

    name: str
    binders: str
    function_type: str

    @property
    def namespace(self) -> str:
        return f"Contracts.{self.name}"


CLAMP = Contract("Clamp", "(v0 : Int) (v1 : Int) (v2 : Int)", "Int → Int → Int → Int")
MULTIPLY_ADD = Contract(
    "MultiplyAdd", "(v0 : Float) (v1 : Float) (v2 : Float) (v3 : Float)", "Float → Float → Float → Float → Bool"
)
INT_IDENTITY = Contract("IntIdentity", "(v0 : Int)", "Int → Int")
SAME_LENGTH = Contract("SameLength", "(v0 : List Int)", "List Int → List Int")
MIXED = Contract("Mixed", "(v0 : Int) (v1 : Bool) (v2 : Float) (v3 : List Int)", "Int → Bool → Float → List Int → Bool")
NINTH = Contract(
    "Ninth",
    "(v0 : Int) (v1 : Int) (v2 : Int) (v3 : Int) (v4 : Int) (v5 : Int) (v6 : Int) (v7 : Int) (v8 : Int)",
    "Int → Int → Int → Int → Int → Int → Int → Int → Int → Int",
)
FLOAT_CLASS = Contract("FloatClass", "(v0 : Float)", "Float → Float")


class Program(BaseModel):
    """An expression body over `v0, v1, …` and its proof.

    The proof names the contract's predicates and the implementation as
    `{pre}`, `{post}` and `{implementation}`.
    """

    implementation: str
    proof: str


def model(contract: Contract, *programs: Program) -> ScriptedModel:
    """A model that defines each program as a declaration, then submits it with its proof."""
    turns = []
    for index, program in enumerate(programs):
        name = f"implementation{index}"
        body = f"fun {contract.binders} => (\n{program.implementation}\n)"
        code = f"open {contract.namespace} in\ndef {name} : {contract.function_type} := (\n{body}\n)"
        proof = program.proof.format(
            pre=f"{contract.namespace}.pre", post=f"{contract.namespace}.post", implementation=f"A.{name}"
        )
        proof = f"by\n  unfold {contract.namespace}.Contract\n  exact (\n{textwrap.indent(proof, '    ')}\n  )"
        turns.append(
            Turn(tool_calls=(("lean", {"code": code}), ("lean_submit", {"answer": f"A.{name}", "proof": proof})))
        )
    return ScriptedModel(turns)


def fixture_project(*, offline=True) -> LeanProject:
    """A fresh, unprepared copy of the fixture project.

    Tests that compile share one prepared copy, the `verified_contracts` fixture.
    """
    return LeanProject(FIXTURE, imports=("Contracts",), offline=offline)


def contract_symbol(contract: Contract, project: LeanProject):
    """The `Contract` symbol of `contract` in a copy of the fixture project."""
    return project.symbols.Contracts[contract.name].Contract


def compile_fixture(contract: Contract, project: LeanProject, **kwargs):
    """`verified.ai_compile` against one of the fixture contracts."""
    return ai_compile(contract=contract_symbol(contract, project), **kwargs)
