from __future__ import annotations

from src.circuit_breaker import MAX_ITERATIONS, assess_cycle, tool_fingerprint
from src.checkpoint import SQLiteCheckpointer
from src.graph import AgentGraph
from src.state import create_state


def test_hash_is_canonical_across_key_order():
    first = tool_fingerprint("execute_wire_transfer", {"recipient_id": "V-8812", "amount_usd": 1500.0})
    second = tool_fingerprint("execute_wire_transfer", {"amount_usd": 1500.0, "recipient_id": "V-8812"})
    assert first == second
    assert len(first) == 64


def test_distinct_payloads_produce_distinct_hashes():
    a = tool_fingerprint("scale_kubernetes_cluster", {"replica_count": 10})
    b = tool_fingerprint("scale_kubernetes_cluster", {"replica_count": 11})
    assert a != b


def test_exact_duplicate_payload_trips_breaker():
    first = tool_fingerprint("query_service_metrics", {"service_name": "auth-service"})
    allowed, reason, _ = assess_cycle(2, [first], "query_service_metrics", {"service_name": "auth-service"})
    assert not allowed and "Duplicate" in reason


def test_recursion_bound_trips_at_six_cycles():
    allowed, reason, _ = assess_cycle(MAX_ITERATIONS, [], "query_service_metrics", {"service_name": "auth-service"})
    assert not allowed and "Cycle budget exceeded" in reason


def test_failing_tool_repeat_stops_safely(graph, session):
    state, _ = graph.submit(session, "Simulate a tool failure", inject_failure=True)
    assert state["status"] == "ERROR"
    assert "Duplicate payload" in state["error"]
    assert 1 <= state["iteration_count"] <= 6
    assert graph.store.operation_count(session) == 0


def test_simulated_mutation_is_idempotent(tmp_path):
    store = SQLiteCheckpointer(tmp_path / "idempotent.db")
    first = store.execute_simulated_once("fixed-key", "thread-one", "execute_wire_transfer", {"amount_usd": 1500})
    second = store.execute_simulated_once("fixed-key", "thread-one", "execute_wire_transfer", {"amount_usd": 1500})
    assert first == second
    assert store.operation_count() == 1
