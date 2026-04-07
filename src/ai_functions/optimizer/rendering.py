"""Rendering utilities for optimizer traces and messages."""

import json
from typing import Any
from xml.sax.saxutils import escape

from strands.types.content import Message

from ..types.graph import Node, ParameterView
from ..utils._formatting import to_yaml, truncate


def render_inputs(nodes: list[Node]) -> str:
    """Render a list of graph nodes as a YAML string for the optimizer prompt."""
    result = {}
    for node in nodes:
        node_type = node.__class__.__name__.lower()
        if isinstance(node, ParameterView) and node.procedural:
            node_type = "code"
        result[node.name] = {
            "type": node_type,
            "description": getattr(node, "description", ""),
            "value": node.value,
        }
        if not result[node.name]["description"]:
            del result[node.name]["description"]
    return to_yaml(result)


def _convert_id(tool_id: str | None, tool_id_to_tool_result_id: dict[str, str]) -> tuple[str, str]:
    call_type = "function" if tool_id is not None and tool_id in tool_id_to_tool_result_id else "tool"
    resolved_id = tool_id_to_tool_result_id.get(tool_id or "", tool_id or "")
    return resolved_id, call_type


def _collect_tool_results(messages: list[Message]) -> dict[str, dict]:
    """Build a map from toolUseId to its result data across all messages."""
    results_map: dict[str, dict] = {}
    for message in messages:
        for block in message.get("content", []):
            if tool_result := block.get("toolResult", None):
                use_id: str | None = tool_result.get("toolUseId")
                if use_id is None:
                    continue
                results = []
                for tool_result_content in tool_result.get("content", []):
                    if text := tool_result_content.get("text"):
                        results.append(truncate(text))
                    elif json_result := tool_result_content.get("json"):
                        results.append(truncate(json_result))
                results_map[use_id] = {
                    "status": tool_result.get("status"),
                    "results": results,
                }
    return results_map


def render_messages(messages: list[Message] | None, tool_id_to_tool_result_id: dict[str, str]) -> str:
    """Format agent messages into a readable conversation trace string."""
    if not messages:
        return ""

    tool_results_map = _collect_tool_results(messages)

    message_list = []
    for i, message in enumerate(messages, 1):
        msg_dict: dict = {
            "role": message.get("role", "unknown").upper(),
            "content": [],
        }
        for block in message.get("content", []):
            if reasoning_text := block.get("reasoningContent", {}).get("text"):
                msg_dict["content"].append({"reasoning": truncate(reasoning_text)})
            if text := block.get("text", ""):
                msg_dict["content"].append({"text": truncate(text)})
            if tool_use := block.get("toolUse", None):
                original_id = tool_use.get("toolUseId")
                _, call_type = _convert_id(original_id, tool_id_to_tool_result_id)
                entry: dict[str, Any] = {
                    "type": f"{call_type}_call",
                    "name": tool_use.get("name"),
                    "inputs": truncate(tool_use.get("input", {})),
                }
                if call_type == "function":
                    entry["id"] = tool_id_to_tool_result_id[original_id]
                # Inline the corresponding result
                if original_id in tool_results_map:
                    result_data = tool_results_map[original_id]
                    entry["status"] = result_data["status"]
                    entry["output"] = result_data["results"]
                msg_dict["content"].append(entry)
            # Skip standalone toolResult blocks — they're inlined above
        if msg_dict["content"]:
            message_list.append({f"message_{i}": msg_dict})

    return to_xml(message_list)


def _format_tool_inputs(inputs: str) -> str:
    """Try to pretty-print JSON tool inputs; fall back to raw string."""
    try:
        parsed = json.loads(inputs) if isinstance(inputs, str) else inputs
        if isinstance(parsed, dict):
            return json.dumps(parsed, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        pass
    return str(inputs)


def to_xml(message_list: list[dict]) -> str:
    """Convert the message_list produced by render_messages into an XML string.

    Each message becomes a ``<message>`` tag with ``number`` and ``role``
    attributes.  Plain text is placed directly inside the tag.
    ``python_executor`` tool calls are rendered as ``<execute_code>`` /
    ``<result>`` pairs; other tool calls use a generic ``<tool_call>`` tag.
    """
    parts: list[str] = []
    for entry in message_list:
        # entry is e.g. {"message_3": {"role": "ASSISTANT", "content": [...]}}
        key = next(iter(entry))
        msg = entry[key]
        number = key.split("_", 1)[1]
        role = msg["role"]

        parts.append(f'<message step="{number}" role="{role}">')

        for block in msg["content"]:
            if isinstance(block, dict) and "reasoning" in block:
                parts.append(f"<reasoning>{escape(str(block['reasoning']))}</reasoning>")

            elif isinstance(block, dict) and "text" in block:
                parts.append(escape(str(block["text"])))

            elif isinstance(block, dict) and "type" in block:
                name = block.get("name", "")
                inputs_raw = block.get("inputs", "")
                status = block.get("status", "")
                output_parts = block.get("output", [])
                output_text = "\n".join(str(o) for o in output_parts)

                if name == "python_executor":
                    # Extract the code from the JSON inputs
                    try:
                        parsed = json.loads(inputs_raw) if isinstance(inputs_raw, str) else inputs_raw
                        code = parsed.get("code", inputs_raw) if isinstance(parsed, dict) else str(inputs_raw)
                    except (json.JSONDecodeError, TypeError):
                        code = str(inputs_raw)
                    parts.append(f"<execute_code>\n{escape(str(code))}\n</execute_code>")
                    if output_text:
                        parts.append(f"<result>\n{escape(output_text)}\n</result>")
                else:
                    formatted_inputs = _format_tool_inputs(inputs_raw)
                    attrs = f' name="{escape(name)}"'
                    if status:
                        attrs += f' status="{escape(str(status))}"'
                    inner = f"<inputs>{escape(formatted_inputs)}</inputs>"
                    if block.get("id"):
                        inner += f"\n<id>{escape(str(block['id']))}</id>"
                    if output_text:
                        inner += f"\n<output>{escape(output_text)}</output>"
                    parts.append(f"<tool_call{attrs}>{inner}</tool_call>")

        parts.append("</message>")

    return "\n".join(parts)
