# AI Generation & Architecture Audit Log

**Project:** StateGuard - Custom Directed State Graph Agent  
**Reviewer:** José Antonio Horta Herrera / AI-assisted review  
**Review scope:** State isolation, canonical hashing, human rejection, mutation authorization, and checkpoint durability.

## 1. Initial AI-assistant prompt

> You are a Principal AI Agent Architect. Scaffold a Python 3.11+ ReAct-style operations agent with explicit graph nodes, typed persistent memory, a maximum of six reasoning cycles, SHA-256 tool payload hashing, safe/sensitive tool classification, SQLite checkpointing, human approval interruptions, a FastAPI web console, and automated tests. Do not use production integrations or real financial transfers. Preserve an initial baseline for code audit and iteratively refactor to deterministic, verifiable transitions.

The short initial code sketch was retained at `audit/baseline_v0.py` to make the review evidence reproducible. **This archived baseline intentionally contains defects and is never imported by the working application.** The completed implementation is in `src/`.

## 2. Finding A - In-place state mutation (high)

**Baseline defect — `audit/baseline_v0.py:flawed_append_message`:**

```python
state["messages"].append({"role": "tool", "content": content})
return state
```

**Why it matters:** A shared list can be mutated outside graph update/reducer semantics. When checkpoint boundaries are introduced, this can create confusing aliasing and partial state updates, especially under retries.

**Refactoring — `src/state.py:copy_state`, `add_message`:**

```diff
- state["messages"].append(message)
- return state
+ new = copy_state(state)  # deep copy before changes
+ new["messages"].append(message)
+ return new
```

**Verification:** `tests/test_graph_flow.py::test_memory_persists_across_turns` and `test_sqlite_checkpointer_survives_engine_restart`.

## 3. Finding B - Noncanonical argument fingerprinting (critical)

**Baseline defect — `audit/baseline_v0.py:flawed_tool_hash`:**

```python
hashlib.sha256(str({"tool": tool_name, "arguments": args}).encode()).hexdigest()
```

**Why it matters:** Python's dictionary string representation is sensitive to insertion order; logically equivalent payloads can evade the repeated-call detector.

**Refactoring — `src/circuit_breaker.py:tool_fingerprint`:**

```diff
- str({"tool": tool_name, "arguments": args}).encode()
+ json.dumps(
+     {"tool": tool_name, "arguments": args},
+     sort_keys=True, separators=(",", ":"), allow_nan=False,
+ ).encode("utf-8")
```

**Verification:** `tests/test_cycle_breaker.py::test_hash_is_canonical_across_key_order`, `test_exact_duplicate_payload_trips_breaker`, and `test_failing_tool_repeat_stops_safely`.

## 4. Finding C - Human rejection loops back to agent (critical)

**Baseline defect — `audit/baseline_v0.py:flawed_rejection_route`:**

```python
state["approval_status"] = "REJECTED"
return "agent_node"
```

**Why it matters:** The previously proposed sensitive tool call remains eligible, so the agent can request approval for the same transfer repeatedly. Rejection is an authorization decision, not merely another observation.

**Refactoring — `src/graph.py:decide` and `_abort_recovery_node`:**

```diff
- if rejected: return "agent_node"
+ if rejected: return "abort_recovery_node"
+ # Write an explicit HUMAN_REJECTED observation, clear pending state,
+ # cancel remaining actions, checkpoint, and transition to END.
```

**Verification:** `tests/test_hitl_approval.py::test_rejection_stops_without_repeat_approval` and `test_rejected_first_action_blocks_remaining_transfer`.

## 5. Finding D - Global thread ID / shared user state (high)

**Baseline defect:**

```python
GLOBAL_THREAD_ID = "default-thread"
```

**Why it matters:** A single identifier allows one operator's facts and approvals to leak into another operator's session.

**Refactoring — `src/graph.py:create_session` and `src/checkpoint.py`:** Each newly created session receives a UUID and SQLite checkpoints are keyed by `thread_id`. There is no hardcoded shared session.

**Verification:** `tests/test_graph_flow.py::test_separate_threads_do_not_leak_memory` and `test_paused_state_survives_restart_and_can_resume`.

## 6. Finding E - Authorization checked only by the UI (critical)

**Baseline defect — `flawed_execute_before_approval`:** Directly invokes a mutation without an independent gate.

**Refactoring:** The execution boundary in `src/graph.py:_tool_executor_node` verifies the approved ticket, run ID, tool name, exact arguments, and canonical SHA-256 payload. The tool layer independently requires `authorized=True` and a stable idempotency key. A SQLite unique ledger prevents replayed simulations from writing twice.

**Verification:** `tests/test_hitl_approval.py::test_sensitive_tool_refuses_direct_unapproved_call`, `test_mismatched_ticket_is_rejected`, and `test_approval_ticket_cannot_be_replayed`.

## 7. Concurrency and persistent state review

- **State persistence:** Checkpoint committed after each node and before the graph returns `PAUSED`.
- **Session updates:** In-process per-session reentrant locks; SQLite optimistic `version` checks and `BEGIN IMMEDIATE` transactions.
- **Sensitive-action idempotency:** Stable operation key + UNIQUE constraint on the ledger.
- **Restart behavior:** A new graph runtime can read the same file and resume the approved or rejected checkpoint.
- **Bounded execution:** Six reasoning cycles maximum, six recent payload hashes, and an additional outer transition cap.
- **Limitations:** This is an educational **local sandbox**. It has no real Kubernetes or banking integrations, no user login, and no cross-host distributed execution lock. It is not approved for production funds or deployments.

## 8. Test evidence

Command:

```bash
uv run pytest -v
```

The repository test suite contains tests for safe autonomous execution, conversation memory, session isolation, conditional routing, pause/resume, approval/rejection, SHA-256 canonicalization, repeated tool failure, cycle limits, idempotency, REST API, and WebSocket snapshots. See `tests/` for the executable assertions. Test results should be regenerated on the submitting machine rather than asserted from screenshots alone.

## 9. Final engineering decision

I retained a **custom directed state graph**, which is an assignment-approved alternative to LangGraph, to expose every transition and policy check in readable Python. I selected **SQLite checkpointing** instead of an in-process-only memory store so an approval pause survives a process restart. The default deterministic planner demonstrates the full reliability architecture without a personal paid-model API key. Sensitive operations are intentionally simulated and cannot modify external systems.

## 10. Optional real LLM adapter

`src/local_model.py` implements on-device Ollama tool calling via the local `/api/chat` endpoint, enabled only with `AGENT_PLANNER=ollama`. The model sees the operator goal and recent observations and proposes the next tool call. The runtime compares the model's tool name and canonical arguments against the prevalidated explicit plan; mismatches are discarded. This allows an actual local model invocation without personal cloud API keys while keeping authorization, loop bounds, and execution routing out of model control. The default offline mode remains deterministic for reproducible tests.
