"""AWS Bedrock AgentCore memory backend for AIFunction parameters."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bedrock_agentcore.memory import MemoryClient, MemorySessionManager
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole

from ..types.graph import ParameterGradient
from .base import MemoryBackend, ParameterMeta, ValueType
from .json_backend import query_value

logger = logging.getLogger(__name__)

# Maximum number of memory records to retrieve in a single operation
MAX_MEMORY_RECORDS = 100

if TYPE_CHECKING:
    from bedrock_agentcore.memory import MemorySession


def _extract_event_texts(events: list) -> list[str]:
    """Extract text content from STM events."""
    return [
        text
        for event in events
        if isinstance(event.get("payload", []), list)
        for item in event.get("payload", [])
        if "conversational" in item
        for text in [item["conversational"].get("content", {}).get("text", "")]
        if text
    ]


def _extract_record_texts(records: list) -> list[str]:
    """Extract text content from LTM records."""
    texts: list[str] = []
    for record in records:
        content = record.get("content", {})
        text = content.get("text", "").strip() if isinstance(content, dict) else str(content).strip()
        if text:
            texts.append(text)
    return texts


def create_memory(name: str, region_name: str = "us-east-1") -> str:
    """Create an AgentCore Memory resource and return its memory_id."""
    client = MemoryClient(region_name=region_name)
    memory = client.create_memory_and_wait(
        name=name,
        description="AI Function memory (parameters, gradients, conversations)",
        strategies=[
            {
                "semanticMemoryStrategy": {
                    "name": "SemanticExtractor",
                    "description": "Extract reusable knowledge from all memory events",
                    "namespaces": ["/{actorId}/"],
                }
            }
        ],
    )
    memory_id: str = memory["id"]
    logger.info("Created memory '%s' with id: %s", name, memory_id)
    return memory_id


def _get_memory_id(name: str, region_name: str = "us-east-1") -> str:
    client = MemoryClient(region_name=region_name)
    memories = client.list_memories()
    for m in memories:
        if m["memoryId"].split("-")[0] == name:
            logger.info("Found existing memory '%s' with id: %s", name, m["memoryId"])
            memory_id: str = m["memoryId"]
            return memory_id
    logger.info("Memory '%s' does not exist. Creating it now.", name)
    return create_memory(name, region_name)


class AgentCoreMemoryBackend(MemoryBackend):
    """AWS Bedrock AgentCore-backed memory for parameters."""

    def _validate_no_procedural_fields(self) -> None:
        """Validate that the schema doesn't contain Procedural fields.

        Raises:
            ValueError: If any field in the schema is marked as Procedural.
        """
        for field_name in self.schema.model_fields.keys():
            if self._is_procedural(field_name):
                raise ValueError(
                    f"AgentCoreMemoryBackend does not support Procedural fields. "
                    f"Field '{field_name}' in schema '{self.schema.__name__}' is marked as Procedural. "
                    f"Please use JSONMemoryBackend for schemas with Procedural parameters."
                )

    def __init__(
        self,
        schema: type,
        actor_id: str,
        memory_id: str | None = None,
        memory_name: str | None = None,
        session_id: str | None = None,
        region_name: str = "us-east-1",
    ) -> None:
        """Initialize AgentCore memory with the given memory ID or name.

        Args:
            schema: Pydantic model schema for memory parameters.
            actor_id: Unique identifier for this actor.
            memory_id: AWS AgentCore memory ID (if already known).
            memory_name: Memory name to get/create (alternative to memory_id).
            session_id: Optional session ID (auto-generated if not provided).
            region_name: AWS region name (default: us-east-1).

        Raises:
            ValueError: If the schema contains Procedural fields (not supported by AgentCore),
                       or if neither memory_id nor memory_name is provided,
                       or if both are provided.
        """
        super().__init__(schema, actor_id)

        # Validate that schema doesn't contain procedural fields
        self._validate_no_procedural_fields()

        # Validate and resolve memory_id
        if memory_id is None and memory_name is None:
            raise ValueError("Either memory_id or memory_name must be provided")
        if memory_id is not None and memory_name is not None:
            raise ValueError("Cannot provide both memory_id and memory_name")

        # Get or resolve memory_id
        if memory_name is not None:
            self.memory_id = _get_memory_id(memory_name, region_name=region_name)
        else:
            assert memory_id is not None  # guaranteed by validation above
            self.memory_id = memory_id

        self.region_name = region_name
        self.session_id = session_id or f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        self.manager = MemorySessionManager(memory_id=self.memory_id, region_name=region_name)
        self._sessions: dict[str, MemorySession] = {}

    def _get_session(self, actor_id: str) -> MemorySession:
        """Get or create a memory session for an actor."""
        if actor_id not in self._sessions:
            self._sessions[actor_id] = self.manager.create_memory_session(
                actor_id=actor_id,
                session_id=self.session_id,
            )
        return self._sessions[actor_id]

    def _retrieve_raw(
        self, actor_id: str, query: str | None = None, top_k: int = MAX_MEMORY_RECORDS
    ) -> tuple[list, list]:
        """Fetch raw STM events and LTM records from AgentCore.

        Retrieves up to top_k items total, balanced between STM and LTM:
        - Tries to get top_k/2 from each source
        - If one source has fewer items, the other can use the remaining quota
        """
        session = self._get_session(actor_id)
        ns = f"/{actor_id}/"
        half_k = top_k // 2

        # Retrieve STM events (up to top_k to see how many are available)
        try:
            stm_events = session.list_events(include_payload=True, max_results=top_k)[::-1]
        except Exception as e:
            logger.warning("retrieve STM error for '%s': %s: %s", actor_id, type(e).__name__, e)
            stm_events = []

        # Calculate how many LTM records to retrieve
        # If STM has fewer than half, LTM can use the rest
        ltm_quota = top_k - min(len(stm_events), half_k)

        # Retrieve LTM records with adjusted quota
        try:
            ltm_records = (
                session.search_long_term_memories(query=query, namespace_prefix=ns, top_k=ltm_quota)
                if query is not None
                else self.manager.list_long_term_memory_records(namespace_prefix=ns, max_results=ltm_quota)
            )
        except Exception as e:
            logger.warning("retrieve LTM error for '%s': %s: %s", actor_id, type(e).__name__, e)
            ltm_records = []

        # Balance the results: take up to half_k from STM, rest from LTM
        stm_take = min(len(stm_events), half_k)
        ltm_take = min(len(ltm_records), top_k - stm_take)

        return stm_events[:stm_take], ltm_records[:ltm_take]

    def _save(self, name: str, value: ValueType) -> None:
        """Save the value of a parameter replacing whatever is currently stored."""
        actor = self._parameter_actor(name)
        # Delete existing entries first (no need to wait — we're writing new data immediately)
        self._delete_records(name, wait=False)

        # Serialize value
        if isinstance(value, list):
            text = "\n\n".join(value)  # Join with double newline
        else:
            text = str(value)

        # Save to AgentCore
        session = self._get_session(actor)
        session.add_turns(
            messages=[ConversationalMessage(text, MessageRole.USER)],
            metadata={"type": {"stringValue": "parameter"}, "name": {"stringValue": name}},
            event_timestamp=datetime.now(UTC),
        )

    def record_counts(self, name: str | None = None) -> tuple[int, int]:
        """Return (stm_count, ltm_count) for a parameter, or across all fields if name is None."""
        if name is not None:
            actor = self._parameter_actor(name)
            events, records = self._retrieve_raw(actor, query=None, top_k=MAX_MEMORY_RECORDS)
            return len(events), len(records)

        total_stm, total_ltm = 0, 0
        for field_name in self.schema.model_fields:
            stm, ltm = self.record_counts(field_name)
            total_stm += stm
            total_ltm += ltm
        return total_stm, total_ltm

    def _wait_until_empty(self, name: str, max_wait: int = 180, poll_interval: int = 5) -> None:
        """Poll until all STM and LTM records for a parameter are gone."""
        import time

        elapsed = 0
        while elapsed < max_wait:
            stm, ltm = self.record_counts(name)
            if stm == 0 and ltm == 0:
                return
            time.sleep(poll_interval)
            elapsed += poll_interval
        logger.warning("Timed out waiting for records to be deleted for '%s'", name)

    def delete(self, name: str) -> None:
        """Delete a parameter by removing all associated STM events and LTM records."""
        self._delete_records(name, wait=True)

    def _delete_records(self, name: str, wait: bool = True) -> None:
        """Delete all STM events and LTM records for a parameter.

        Args:
            name: Parameter name to delete.
            wait: If True, polls until all records are confirmed deleted.
        """
        actor = self._parameter_actor(name)
        ns = f"/{actor}/"
        events, records = self._retrieve_raw(actor, query=None, top_k=MAX_MEMORY_RECORDS)

        if not events and not records:
            return

        # Delete STM events (no bulk API available)
        for eid in (e.get("eventId") for e in events):
            if eid:
                try:
                    self.manager.delete_event(actor_id=actor, session_id=self.session_id, event_id=eid)
                except Exception as e:
                    logger.warning("Failed to delete STM event '%s': %s", eid, e)

        # Bulk delete LTM records via namespace
        if records:
            try:
                self.manager.delete_all_long_term_memories_in_namespace(namespace=ns)
            except Exception as e:
                logger.warning("Failed to bulk delete LTM records in '%s': %s", ns, e)

        if wait:
            self._wait_until_empty(name)

    def delete_all(self, wait: bool = False) -> None:
        """Delete all memories for every field in the schema for this actor.

        Fires all deletes without waiting, then polls until every field is empty.
        """
        for field_name in self.schema.model_fields:
            self._delete_records(field_name, wait=False)
        if wait:
            for field_name in self.schema.model_fields:
                self._wait_until_empty(field_name)

    def _recall(self, name: str) -> tuple[ValueType, ParameterMeta]:
        """Return (value, meta) for a parameter from storage."""
        actor = self._parameter_actor(name)
        events, records = self._retrieve_raw(actor, query=None, top_k=MAX_MEMORY_RECORDS)

        all_texts = _extract_event_texts(events) + _extract_record_texts(records)

        if not all_texts:
            # Return default from schema by creating an instance
            instance = self.schema()
            return getattr(instance, name), {}

        concatenated = "\n\n".join(all_texts)

        # Check if field is list[str] type by inspecting the default value type
        instance = self.schema()
        default_value = getattr(instance, name)
        if isinstance(default_value, list):
            # Split back into list
            return [item.strip() for item in concatenated.split("\n\n") if item.strip()], {}

        return concatenated, {}

    def _search(self, name: str, query: str, k: int = 5) -> tuple[list[str], ParameterMeta]:
        """Return (top_k_values, meta) for a search against a list parameter."""
        actor = self._parameter_actor(name)
        # Use _retrieve_raw with semantic query to get relevant records
        events, records = self._retrieve_raw(actor, query=query, top_k=k)

        # Extract and combine texts from both sources
        all_texts = _extract_event_texts(events) + _extract_record_texts(records)
        return all_texts, {}

    def _query(self, name: str, query: str) -> tuple[str, ParameterMeta]:
        """Return (answer, meta) for a query against parameter *name*."""
        # Search for relevant content (top 10 results)
        relevant_texts, _ = self._search(name, query, k=10)

        if not relevant_texts:
            return "", {}

        # Concatenate relevant content
        content = "\n\n".join(relevant_texts)

        # Use AI to answer the query based on the content
        return query_value(content, query), {}

    def _consolidate(self, name: str, feedback: list[ParameterGradient]) -> None:
        """Add feedback to the parameter, letting AgentCore's semantic memory extract and consolidate.

        Unlike JSONMemoryBackend which uses AI to explicitly consolidate, AgentCore's
        semantic memory strategy automatically extracts and consolidates information from
        conversation turns into long-term memory.
        """
        if not feedback:
            return

        actor = self._parameter_actor(name)
        session = self._get_session(actor)

        # Add each feedback item as a conversation turn
        # AgentCore's semantic memory extractor will process these and consolidate into LTM
        for gradient in feedback:
            session.add_turns(
                messages=[ConversationalMessage(gradient.feedback, MessageRole.USER)],
                metadata={"type": {"stringValue": "feedback"}, "name": {"stringValue": name}},
                event_timestamp=datetime.now(UTC),
            )

    def close(self) -> None:
        """Release resources held by this backend."""
        logger.info("AgentCoreMemoryBackend closed (memory_id: %s)", self.memory_id)

    def __str__(self) -> str:
        """Return a YAML representation of the current memory state."""
        from ..utils._formatting import to_yaml

        # Build a dictionary of all parameter values
        data = {}
        for field_name in self.schema.model_fields.keys():
            value, _ = self._recall(field_name)
            data[field_name] = value

        # Create a temporary model instance for formatting
        model_instance = self.schema(**data)
        return to_yaml(model_instance.model_dump())
