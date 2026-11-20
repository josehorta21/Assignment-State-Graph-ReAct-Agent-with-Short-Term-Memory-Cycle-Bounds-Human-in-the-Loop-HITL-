from __future__ import annotations

import pytest

from src.graph import GraphInputError
from src.tools import execute_tool


def test_sensitive_action_interrupts_before_execution(graph, session):
    state, _ = graph.submit(session, "Transfer $1500 to V-8812")
    assert state["status"] == "PAUSED"
    assert state["pending_approval"]["tool_name"] == "execute_wire_transfer"
    assert state["current_node"] == "human_approval_gate"
    assert graph.store.operation_count(session) == 0


def test_approval_resumes_matching_checkpoint(graph, session):
    paused, _ = graph.submit(session, "Transfer $1500 to V-8812")
    final, _ = graph.decide(session, paused["pending_approval"]["ticket_id"], "APPROVED")
    assert final["status"] == "COMPLETED"
    assert final["pending_approval"] is None
    assert graph.store.operation_count(session) == 1


def test_rejection_stops_without_repeat_approval(graph, session):
    paused, _ = graph.submit(session, "Transfer $1500 to V-8812")
    final, _ = graph.decide(session, paused["pending_approval"]["ticket_id"], "REJECTED")
    assert final["status"] == "REJECTED"
    assert final["pending_approval"] is None
    assert "safely aborted" in final["last_response"]
    assert "abort_recovery_node" in [e["node"] for e in final["event_log"]]
    assert graph.store.operation_count(session) == 0


def test_approval_ticket_cannot_be_replayed(graph, session):
    paused, _ = graph.submit(session, "Transfer $1500 to V-8812")
    ticket = paused["pending_approval"]["ticket_id"]
    graph.decide(session, ticket, "APPROVED")
    with pytest.raises(GraphInputError, match="No pending approval"):
        graph.decide(session, ticket, "APPROVED")
    assert graph.store.operation_count(session) == 1


def test_mismatched_ticket_is_rejected(graph, session):
    graph.submit(session, "Transfer $1500 to V-8812")
    with pytest.raises(GraphInputError, match="Stale or mismatched"):
        graph.decide(session, "fake-ticket-not-a-match", "APPROVED")
    assert graph.store.operation_count(session) == 0


def test_new_prompt_cannot_bypass_pending_approval(graph, session):
    graph.submit(session, "Transfer $1500 to V-8812")
    with pytest.raises(GraphInputError, match="Resolve the pending"):
        graph.submit(session, "Check auth-service error rate")


def test_sensitive_tool_refuses_direct_unapproved_call(graph, session):
    with pytest.raises(PermissionError, match="matching approved"):
        execute_tool(
            "execute_wire_transfer", {"recipient_id": "V-8812", "amount_usd": 1500.0},
            store=graph.store, thread_id=session,
        )
    assert graph.store.operation_count(session) == 0


def test_rejected_first_action_blocks_remaining_transfer(graph, session):
    paused, _ = graph.submit(
        session,
        "Check auth-service error rate. If over 5%, scale its replicas to 10 and transfer $1500 to V-8812.",
    )
    assert paused["pending_approval"]["tool_name"] == "scale_kubernetes_cluster"
    state, _ = graph.decide(session, paused["pending_approval"]["ticket_id"], "REJECTED")
    assert state["status"] == "REJECTED"
    assert graph.store.operation_count(session) == 0
