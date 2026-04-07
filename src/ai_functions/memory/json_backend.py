"""JSON memory backend backed by TinyDB for stable document IDs."""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from strands import tool
from strands.models import Model
from strands.tools import ToolProvider
from strands.types.tools import AgentTool
from tinydb import Query, TinyDB
from tinydb.table import Document, Table

from .. import ai_function
from ..tools.local_python_executor import SAFE_BUILTINS  # noqa: F401
from ..types.graph import ParameterGradient
from ..utils import bullet_points as bullet_points  # noqa: F401
from ..utils import quiet_console
from ..utils import to_yaml as to_yaml  # noqa: F401
from .base import DynamicToolProvider, MemoryBackend, ParameterMeta, ValueType
from .procedural import Procedural, validate_procedural
from .utils import flatten_schema, is_list_field, unflatten_fields


@ai_function(callback_handler=None)
def consolidate_list(memories: dict[int, str], feedback: list[str]) -> Literal["done"]:  # type: ignore[empty-body]
    """You are a memory manager. The memories listed below were retrieved from a memory store
    because they are relevant to the feedback. Use the provided tools to search, add, update,
    or delete memories as needed to incorporate the feedback.

    <retrieved_memories>
    {to_yaml(memories)}
    </retrieved_memories>

    <feedback>
    {bullet_points(feedback)}
    </feedback>

    After you have applied all necessary changes using the tools, return "done".
    """
    ...


@ai_function(callback_handler=None)
def consolidate_value(value: str, feedback: list[str]) -> str:  # type: ignore[empty-body]
    """Please update the following value with the feedback provided below.
    The feedback could describe information to add, change, or consolidate.
    Return the updated value.

    <value>
    {value}
    </value>

    <feedback>
    {bullet_points(feedback)}
    </feedback>
    """
    ...


def validate(result: str) -> None:
    """Validate procedural code output."""
    validate_procedural(result)


@ai_function(callback_handler=None, post_conditions=[validate])
def consolidate_procedural(value: str, feedback: list[str]) -> Procedural:  # type: ignore[empty-body]
    """The following code contains functions that an AI agent could find useful
    to call accomplish a task. The code will be executed during the agent initialization
    to make these functions available inside its Python environment.

    <code>
    {value}
    </code>

    We have received the following feedback on regarding what to add, remove or how to
    modify the code:

    <feedback>
    {bullet_points(feedback)}
    </feedback>

    Please write and return a new version of the code incorporating the feedback above.
    <rules>
    - You need to rewrite the entire code, not only the changes
    - Keep the code clean and minimal. Remove near-duplicated functions. Create reusable abstractions.
    - There is no need to maintain backward compatibility. Rewrite the code in the best possible way.
    - You can only import the following modules: {", ".join(SAFE_BUILTINS)}
    - Your answer MUST be a string containing valid Python-code that can be executed as is.
    - DO NOT enclose your answer in Markdown formatting like ```python
    - The code MUST only contain function definitions. DO NOT define classes.
    - Each function should be simple (a few lines long) and modular.
    - Do not use complex syntax. Do not use advanced Python features.
    </rules>
    <docstring_rules>
    Functions that the agent should call directly should have a docstring containing:
    - A description of when to use the function
    - Examples of inputs to which the function is applicable
    - Example of the expected output
    The agent should be able to decide whether to use the function by only looking at the docstring

    The name of utility functions that the agent should not call directly MUST start with an underscore _
    </docstring_rules>
    """
    ...


@ai_function(callback_handler=None)
def query_value(value: str, query: str) -> str:  # type: ignore[empty-body]
    """Based on the content below, please answer the following question:
    <question>
    {query}
    </question>

    <content>
    {value}
    </content>
    """
    ...


class JSONMemoryBackend(MemoryBackend):
    """Memory backend using TinyDB for JSON persistence."""

    def __init__(
        self,
        schema: type[BaseModel],
        actor_id: str,
        path: Path | str,
        model: Model | str | None = None,
        quiet: bool = True,
    ) -> None:
        """Initialize the JSON memory backend.

        Args:
            schema: Pydantic model defining the memory structure.
            actor_id: Unique identifier for the actor.
            path: File path for the TinyDB JSON store.
            model: LLM model for consolidation operations.
            quiet: Whether to suppress console output.
        """
        super().__init__(schema, actor_id)
        self.quiet = quiet

        self.consolidate_value = consolidate_value.replace(model=model)
        self.consolidate_procedural = consolidate_procedural.replace(model=model)
        self.consolidate_list = consolidate_list.replace(model=model)
        self.query_value = query_value.replace(model=model)

        self._db = TinyDB(Path(path))
        self._scalars: Table = self._db.table(f"{actor_id}/_scalars")
        self._leaf_fields = flatten_schema(schema)
        if not self._actor_exists():
            self._seed_defaults()

    def __str__(self) -> str:
        """Return a YAML representation of the memory contents."""
        return to_yaml(self._hydrate().model_dump())

    def _table(self, name: str) -> Table:
        """Return the TinyDB table for a list parameter, namespaced by actor_id."""
        return self._db.table(f"{self.actor_id}/{name}")

    def _actor_exists(self) -> bool:
        """Return True if this actor already has data in the database."""
        return len(self._scalars) > 0 or any(
            len(self._table(path)) > 0 for path, fi in self._leaf_fields if is_list_field(fi)
        )

    def _seed_defaults(self) -> None:
        """Persist schema defaults for a new actor."""
        Q = Query()
        for path, field_info in self._leaf_fields:
            default = field_info.default
            if is_list_field(field_info):
                table = self._table(path)
                if default:
                    for v in default:
                        table.insert({"value": v})
            else:
                if default is not None:
                    self._scalars.upsert({"name": path, "value": default}, Q.name == path)

    def _hydrate(self) -> BaseModel:
        """Rebuild the Pydantic model from TinyDB tables."""
        flat: dict[str, Any] = {}
        for doc in self._scalars.all():
            flat[doc["name"]] = doc["value"]
        for path, field_info in self._leaf_fields:
            if path not in flat and is_list_field(field_info):
                flat[path] = [doc["value"] for doc in self._table(path).all()]
        for path, field_info in self._leaf_fields:
            if path not in flat:
                flat[path] = field_info.default
        return self.schema.model_validate(unflatten_fields(flat))

    def dump(self) -> BaseModel:
        """Return the entire memory content as the underlying Pydantic model."""
        return self._hydrate()

    # Utilities to read from TinyDB

    def _read_scalar(self, name: str) -> str:
        """Read a scalar value from the _scalars table."""
        Q = Query()
        doc = self._scalars.get(Q.name == name)
        if doc is None:
            default = self._resolve_field(name).default
            return str(default) if default is not None else ""
        return str(doc["value"])  # type: ignore[call-overload]

    def _read_list(self, name: str) -> list[str]:
        """Read all values from a list table."""
        return [str(doc["value"]) for doc in self._table(name).all()]

    def _read_value(self, name: str) -> ValueType:
        """Read a value, dispatching to scalar or list based on schema."""
        field_info = self._resolve_field(name)
        if is_list_field(field_info):
            return self._read_list(name)
        return self._read_scalar(name)

    # MemoryBackend abstract method implementations

    def _save(self, name: str, value: ValueType) -> None:
        if isinstance(value, list):
            table = self._table(name)
            table.truncate()
            for v in value:
                table.insert({"value": v})
        else:
            Q = Query()
            self._scalars.upsert({"name": name, "value": value}, Q.name == name)

    def _recall(self, name: str) -> tuple[ValueType, ParameterMeta]:
        return self._read_value(name), {}

    def _query(self, name: str, query: str) -> tuple[str, ParameterMeta]:
        value = self._read_value(name)
        with quiet_console(self.quiet):
            answer = self.query_value(str(value), query)
        return answer, {}

    def _search(self, name: str, query: str, k: int = 5) -> tuple[list[str], ParameterMeta]:
        """Return (top_k_values, meta) with meta containing {doc_id: value} mapping."""
        docs = self._search_docs(name, query, k)
        results = {d.doc_id: d["value"] for d in docs}
        return [d["value"] for d in docs], {"results": results}

    def _search_docs(self, name: str, query: str, k: int = 5) -> list[Document]:
        """Return top-k TinyDB Document objects ranked by BM25."""
        from rank_bm25 import BM25Okapi

        table = self._table(name)
        all_docs = table.all()
        if not all_docs:
            return []

        corpus = [d["value"] for d in all_docs]
        tokenized = [v.lower().split() for v in corpus]
        bm25 = BM25Okapi(tokenized)
        scores = bm25.get_scores(query.lower().split())
        scored = sorted(zip(all_docs, scores, strict=False), key=lambda x: x[1], reverse=True)
        return [doc for doc, _ in scored[:k]]

    def delete(self, name: str) -> None:
        """Delete a parameter."""
        field_info = self._resolve_field(name)
        if is_list_field(field_info):
            self._table(name).truncate()
        else:
            Q = Query()
            self._scalars.remove(Q.name == name)

    def _consolidate(self, name: str, feedback: list[ParameterGradient]) -> None:
        texts = [g.feedback for g in feedback]
        value = self._read_value(name)
        with quiet_console(self.quiet):
            if self._is_procedural(name):
                assert isinstance(value, str)
                value = self.consolidate_procedural(value, texts)
                self._save(name, value)
            elif isinstance(value, list):
                # Merge {doc_id: value} dicts from all search derivations
                retrieved: dict[int, str] = {}
                for g in feedback:
                    for doc_id, val in g.derivation.meta.get("results", {}).items():
                        retrieved[doc_id] = val
                # Fall back to all docs if no search derivations
                if not retrieved:
                    retrieved = {d.doc_id: d["value"] for d in self._table(name).all()}
                fn = self.consolidate_list.replace(tools=[MemoryToolProvider(self, name)])
                fn(retrieved, texts)
            else:
                assert isinstance(value, str)
                value = self.consolidate_value(value, texts)
                self._save(name, value)

    def tool_provider(self, *names: str, operations: set[str] | None = None) -> "DynamicToolProvider":
        """Extend base tool_provider with doc_id-based list CRUD tools."""
        from .utils import is_list_field

        ops = operations or {"recall", "query", "search", "save", "delete", "add", "update"}
        provider = super().tool_provider(*names, operations=ops)
        extra: list = []
        for name in names:
            field_info = self._resolve_field(name)
            if not is_list_field(field_info):
                continue
            desc = self._get_description(name) or name
            safe = name.replace("/", "_")

            if "add" in ops:
                extra.append(
                    tool(name=f"add_to_{safe}", description=f"Add a new entry to: {desc}")(self._make_list_add(name))
                )
            if "update" in ops:
                extra.append(
                    tool(name=f"update_{safe}", description=f"Update an entry by doc_id in: {desc}")(
                        self._make_list_update(name)
                    )
                )
            if "delete" in ops:
                extra.append(
                    tool(name=f"delete_from_{safe}", description=f"Delete an entry by doc_id from: {desc}")(
                        self._make_list_delete(name)
                    )
                )

        return DynamicToolProvider(provider._tools + extra)

    def _make_list_add(self, param_name: str) -> Callable[[str], str]:
        def _add(value: str) -> str:
            """Add a new entry to this list.

            Args:
                value: The text content of the new entry.
            """
            doc_id = self._list_add(param_name, value)
            return f"Added with doc_id={doc_id}"

        return _add

    def _make_list_update(self, param_name: str) -> Callable[..., str]:
        def _update(doc_id: int, value: str) -> str:
            """Update an existing entry by its stable doc_id.

            Args:
                doc_id: The stable identifier of the entry to update.
                value: The new text content.
            """
            if not self._list_update(param_name, doc_id, value):
                raise ValueError(f"doc_id={doc_id} not found")
            return f"Updated doc_id={doc_id}"

        return _update

    def _make_list_delete(self, param_name: str) -> Callable[[int], str]:
        def _delete(doc_id: int) -> str:
            """Delete an entry by its stable doc_id.

            Args:
                doc_id: The stable identifier of the entry to delete.
            """
            if not self._list_remove(param_name, doc_id):
                raise ValueError(f"doc_id={doc_id} not found")
            return f"Deleted doc_id={doc_id}"

        return _delete

    def close(self) -> None:
        """Close the TinyDB database."""
        self._db.close()

    # List CRUD operations used as tools by the consolidate_list agent

    def _list_add(self, name: str, value: str) -> int:
        """Insert a new entry and return its stable doc_id."""
        return int(self._table(name).insert({"value": value}))

    def _list_update(self, name: str, doc_id: int, value: str) -> bool:
        """Update an entry by doc_id. Returns True on success."""
        table = self._table(name)
        if table.get(doc_id=doc_id) is None:
            return False
        table.update({"value": value}, doc_ids=[doc_id])
        return True

    def _list_remove(self, name: str, doc_id: int) -> bool:
        """Remove an entry by doc_id. Returns True on success."""
        table = self._table(name)
        if table.get(doc_id=doc_id) is None:
            return False
        table.remove(doc_ids=[doc_id])
        return True


class MemoryToolProvider(ToolProvider):
    """Provides CRUD tools scoped to a single list[str] parameter on a JSONMemoryBackend."""

    def __init__(self, backend: JSONMemoryBackend, name: str) -> None:
        """Initialize with a backend and parameter name."""
        self._backend = backend
        self._name = name
        self._consumers: set[Any] = set()

    @tool
    def search_memories(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Search memories by keyword relevance. Returns a list of {doc_id, value} dicts."""
        return [{"doc_id": d.doc_id, "value": d["value"]} for d in self._backend._search_docs(self._name, query, k)]

    @tool
    def add_memory(self, value: str) -> str:
        """Add a new memory entry to the list. Returns the doc_id of the new entry."""
        doc_id = self._backend._list_add(self._name, value)
        return f"Added with doc_id={doc_id}"

    @tool
    def update_memory(self, doc_id: int, value: str) -> str:
        """Update an existing memory entry by its stable doc_id."""
        if not self._backend._list_update(self._name, doc_id, value):
            raise ValueError(f"doc_id={doc_id} not found")
        return f"Updated doc_id={doc_id}"

    @tool
    def delete_memory(self, doc_id: int) -> str:
        """Delete a memory entry by its stable doc_id."""
        if not self._backend._list_remove(self._name, doc_id):
            raise ValueError(f"doc_id={doc_id} not found")
        return f"Deleted doc_id={doc_id}"

    async def load_tools(self, **kwargs: Any) -> Sequence[AgentTool]:
        """Return the CRUD tools for the memory list."""
        return [self.search_memories, self.add_memory, self.update_memory, self.delete_memory]

    def add_consumer(self, consumer_id: Any, **kwargs: Any) -> None:
        """Add a consumer."""
        pass

    def remove_consumer(self, consumer_id: Any, **kwargs: Any) -> None:
        """Remove a consumer."""
        pass
