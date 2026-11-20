"""Optional Ollama-backed tool-call reasoning. Does not require external API keys.

The model proposes an action; deterministic graph policy validates the exact tool
and arguments before it can proceed. The local model is never an authorization
source and cannot bypass human approval or the circuit breaker.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from .circuit_breaker import tool_fingerprint
from .state import ToolCall


class LocalModelError(RuntimeError):
    pass


# Only localhost is supported; do not send user requests to arbitrary endpoints.
LOCAL_URL = "http://127.0.0.1:11434/api/chat"


def propose_next_tool(
    user_goal: str,
    next_allowed: ToolCall,
    observations: list[dict[str, Any]],
) -> tuple[bool, str]:
    """Ask the local model to bind a tool call; return only validated advice.

    Model output never changes which tools the verified plan permits.
    A mismatch is rejected and the deterministic planner remains authoritative.
    """
    next_name = next_allowed["name"]
    arguments = next_allowed["arguments"]
    tool_schema = {
        "type": "function",
        "function": {
            "name": next_name,
            "description": "Choose only this explicitly approved-by-policy next tool if it serves the goal.",
            "parameters": {
                "type": "object",
                "properties": {key: {"type": ("number" if isinstance(value, (float, int)) else "string")} for key, value in arguments.items()},
                "required": list(arguments),
            },
        },
    }
    payload = {
        "model": os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
        "stream": False,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": (
                "You are a local operations-agent reasoning module. Your job is to propose "
                "the next tool call ONLY if it is warranted by the operator's objective "
                "and prior tool observations. You cannot change the supplied arguments, "
                "invent new tool calls, or authorize sensitive operations. "
                "Do not reveal hidden reasoning."
            )},
            {"role": "user", "content": json.dumps({
                "original_goal": user_goal,
                "recent_observations": observations[-3:],
                "next_validated_action": {"name": next_name, "arguments": arguments},
            })},
        ],
        "tools": [tool_schema],
    }
    request = Request(
        LOCAL_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=3.5) as response:
            body = json.loads(response.read(1024 * 1024).decode("utf-8"))
    except (OSError, URLError, ValueError, TimeoutError) as exc:
        raise LocalModelError("Local Ollama model is unavailable; deterministic fallback used") from exc
    calls = body.get("message", {}).get("tool_calls", [])
    if not isinstance(calls, list) or len(calls) != 1:
        return False, "Local LLM did not select exactly one permitted tool; proposal ignored"
    function = calls[0].get("function", {})
    name = function.get("name")
    proposed = function.get("arguments", {})
    if isinstance(proposed, str):
        try:
            proposed = json.loads(proposed)
        except ValueError:
            proposed = {}
    if not isinstance(proposed, dict) or name != next_name:
        return False, "Local LLM proposed an unexpected tool; proposal rejected"
    try:
        same_fingerprint = tool_fingerprint(name, proposed) == tool_fingerprint(next_name, arguments)
    except (TypeError, ValueError):
        same_fingerprint = False
    if not same_fingerprint:
        return False, "Local LLM changed tool arguments; proposal rejected"
    return True, "Ollama returned a matching tool call; graph runtime independently enforces policy"
