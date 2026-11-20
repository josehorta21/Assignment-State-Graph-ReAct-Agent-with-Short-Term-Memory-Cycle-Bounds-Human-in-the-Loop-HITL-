from __future__ import annotations

from src.checkpoint import SQLiteCheckpointer
from src.graph import AgentGraph

from .conftest import SAMPLE_INCIDENT


def test_safe_tool_traverses_without_approval(graph, session):
    state, _ = graph.submit(session, "Check the error rate on auth-service")
    assert state["status"] == "COMPLETED"
    assert state["pending_approval"] is None
    nodes = [entry["node"] for entry in state["event_log"]]
    assert "agent_node" in nodes and "circuit_breaker_node" in nodes
    assert "tool_executor_node" in nodes and "END" in nodes
    assert state["last_metric"]["auth-service"] == 8.2


def test_memory_persists_across_turns(graph, session):
    first, _ = graph.submit(session, "My name is Alice")
    assert first["facts"]["user_name"] == "Alice"
    second, _ = graph.submit(session, "What is my name?")
    assert second["status"] == "COMPLETED"
    assert "Alice" in second["last_response"]


def test_separate_threads_do_not_leak_memory(graph):
    thread_a = graph.create_session()["thread_id"]
    thread_b = graph.create_session()["thread_id"]
    graph.submit(thread_a, "My name is Alice")
    state, _ = graph.submit(thread_b, "What is my name?")
    assert "do not have your name" in state["last_response"]
    assert "user_name" not in state["facts"]


def test_low_error_rate_skips_sensitive_scale(graph, session):
    state, _ = graph.submit(
        session, "Check the error rate on healthy-service. If over 5%, scale its replicas to 10."
    )
    assert state["status"] == "COMPLETED"
    assert graph.store.operation_count(session) == 0
    assert any(r.get("skipped") for r in state["tool_results"])
    assert "threshold" in state["last_response"].lower()


def test_incident_requests_two_distinct_approvals(graph, session):
    state, _ = graph.submit(session, SAMPLE_INCIDENT)
    assert state["status"] == "PAUSED"
    assert state["pending_approval"]["tool_name"] == "scale_kubernetes_cluster"
    state, _ = graph.decide(session, state["pending_approval"]["ticket_id"], "APPROVED")
    assert state["status"] == "PAUSED"
    assert state["pending_approval"]["tool_name"] == "execute_wire_transfer"
    assert graph.store.operation_count(session) == 1
    state, _ = graph.decide(session, state["pending_approval"]["ticket_id"], "APPROVED")
    assert state["status"] == "COMPLETED"
    assert graph.store.operation_count(session) == 2
    assert "SIMULATED" in state["last_response"]


def test_sqlite_checkpointer_survives_engine_restart(graph, session):
    graph.submit(session, "My name is Alice")
    restored = AgentGraph(SQLiteCheckpointer(graph.store.db_path))
    state, _ = restored.submit(session, "What is my name?")
    assert "Alice" in state["last_response"]


def test_paused_state_survives_restart_and_can_resume(graph, session):
    state, _ = graph.submit(session, "Transfer $1500 to V-8812")
    assert state["status"] == "PAUSED"
    restored = AgentGraph(SQLiteCheckpointer(graph.store.db_path))
    state, _ = restored.decide(session, state["pending_approval"]["ticket_id"], "APPROVED")
    assert state["status"] == "COMPLETED"
    assert restored.store.operation_count(session) == 1
