"""Typed, JSON-serializable state schema and immutable-update helpers."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Literal, NotRequired, TypedDict
from uuid import uuid4


Node = Literal[
    "START", "agent_node", "circuit_breaker_node", "human_approval_gate",
    "tool_executor_node", "error_node", "abort_recovery_node", "END",
]
Status = Literal["READY", "RUNNING", "PAUSED", "COMPLETED", "REJECTED", "ERROR"]


class ToolCall(TypedDict):
    name: str
    arguments: dict[str, Any]
    condition: dict[str, Any] | None
    retry_on_error_demo: bool


class ChatMessage(TypedDict):
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    timestamp: str
    tool_name: NotRequired[str]


class ApprovalTicket(TypedDict):
    ticket_id: str
    run_id: str
    tool_name: str
    arguments: dict[str, Any]
    payload_hash: str
    risk: str
    submitted_at: str


class AgentState(TypedDict):
    thread_id: str
    run_id: str
    messages: list[ChatMessage]
    iteration_count: int
    executed_tool_hashes: list[str]
    pending_approval: ApprovalTicket | None
    approval_status: Literal["APPROVED", "REJECTED"] | None
    current_node: Node
    status: Status
    plan: list[ToolCall]
    plan_cursor: int
    pending_tool: ToolCall | None
    last_metric: dict[str, float]
    facts: dict[str, str]
    event_log: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    error: str | None
    last_response: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_state(thread_id: str) -> AgentState:
    return AgentState(
        thread_id=thread_id,
        run_id="",
        messages=[],
        iteration_count=0,
        executed_tool_hashes=[],
        pending_approval=None,
        approval_status=None,
        current_node="START",
        status="READY",
        plan=[],
        plan_cursor=0,
        pending_tool=None,
        last_metric={},
        facts={},
        event_log=[],
        tool_results=[],
        error=None,
        last_response="",
    )


def copy_state(state: AgentState, **updates: Any) -> AgentState:
    """Never mutate a checkpointed state in-place."""
    result = deepcopy(state)
    result.update(updates)
    return result


def add_message(state: AgentState, role: str, content: str, **extras: str) -> AgentState:
    new = copy_state(state)
    message: ChatMessage = {"role": role, "content": content, "timestamp": utc_now()}  # type: ignore[typeddict-item]
    message.update(extras)  # type: ignore[typeddict-item]
    new["messages"].append(message)
    # Bound retained conversation context without removing current-run events.
    new["messages"] = new["messages"][-80:]
    return new


def record_event(state: AgentState, node: Node, detail: str, kind: str = "info") -> AgentState:
    result = copy_state(state, current_node=node)
    result["event_log"].append(
        {"timestamp": utc_now(), "node": node, "detail": detail, "kind": kind, "run_id": state["run_id"]}
    )
    result["event_log"] = result["event_log"][-120:]
    return result


def new_run_id() -> str:
    return str(uuid4())
