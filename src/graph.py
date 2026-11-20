"""Explicit cyclic state graph with durable HITL interrupts and bounded execution.

The graph is custom (not LangGraph) and uses a deterministic offline planner.
Each transition checkpoints SQLite; sensitive tools can execute only after an
approved ticket whose tool fingerprint matches the suspended request.
"""

from __future__ import annotations

from copy import deepcopy
import os
from typing import Any
from uuid import uuid4

from .checkpoint import SQLiteCheckpointer
from .circuit_breaker import MAX_ITERATIONS, assess_cycle, tool_fingerprint
from .planner import build_plan, condition_passes
from .local_model import LocalModelError, propose_next_tool
from .state import AgentState, Node, add_message, copy_state, new_run_id, record_event, utc_now
from .tools import REGISTRY, ToolExecutionError, execute_tool, validate_tool


class GraphInputError(ValueError):
    """An invalid operation or transition was requested."""


class AgentGraph:
    """Finite-state execution with fixed routing and bounded graph transitions."""

    def __init__(self, store: SQLiteCheckpointer, max_iterations: int = MAX_ITERATIONS) -> None:
        self.store = store
        self.max_iterations = min(max(1, max_iterations), MAX_ITERATIONS)

    def create_session(self) -> AgentState:
        from .state import create_state

        thread_id = str(uuid4())
        state = create_state(thread_id)
        self.store.save(state, 0)
        return state

    def get_session(self, thread_id: str) -> tuple[AgentState, int]:
        state, version = self.store.load(thread_id)
        if version == 0:
            raise GraphInputError("Unknown session. Create a new session first.")
        return state, version

    def submit(self, thread_id: str, text: str, *, inject_failure: bool = False) -> tuple[AgentState, int]:
        if not 1 <= len(text.strip()) <= 2000:
            raise GraphInputError("Enter a prompt between 1 and 2,000 characters.")
        with self.store.lock_for(thread_id):
            state, version = self.get_session(thread_id)
            if state["status"] == "PAUSED":
                raise GraphInputError("Resolve the pending human approval before starting another request.")
            if state["status"] == "RUNNING":
                raise GraphInputError("This session is already processing a request.")
            run_id = new_run_id()
            state = copy_state(
                state,
                run_id=run_id,
                status="RUNNING",
                iteration_count=0,
                executed_tool_hashes=[],
                pending_approval=None,
                approval_status=None,
                current_node="START",
                plan=[],
                plan_cursor=0,
                pending_tool=None,
                tool_results=[],
                error=None,
                last_response="",
            )
            state = add_message(state, "user", text.strip())
            state = record_event(state, "START", "New request accepted; session checkpoint opened.")
            version = self.store.save(state, version)
            try:
                plan = build_plan(text, state["facts"], inject_failure=inject_failure)
                facts = deepcopy(state["facts"])
                facts.update(plan.fact_updates)
                state = copy_state(state, plan=plan.calls, facts=facts)
                version = self.store.save(state, version)
                if plan.immediate_response:
                    state = record_event(state, "agent_node", "Resolved informational request without tool calls.")
                    state = add_message(state, "assistant", plan.immediate_response)
                    state = copy_state(state, status="COMPLETED", last_response=plan.immediate_response)
                    state = record_event(state, "END", "Execution completed without tools.", "success")
                    version = self.store.save(state, version)
                    return state, version
                return self._drive(state, version, "agent_node")
            except (ToolExecutionError, ValueError) as exc:
                return self._fail(state, version, str(exc))

    def decide(
        self, thread_id: str, ticket_id: str, decision: str
    ) -> tuple[AgentState, int]:
        if decision not in ("APPROVED", "REJECTED"):
            raise GraphInputError("Decision must be APPROVED or REJECTED.")
        with self.store.lock_for(thread_id):
            state, version = self.get_session(thread_id)
            ticket = state["pending_approval"]
            if state["status"] != "PAUSED" or ticket is None:
                raise GraphInputError("No pending approval exists for this session.")
            if ticket["ticket_id"] != ticket_id or ticket["run_id"] != state["run_id"]:
                raise GraphInputError("Stale or mismatched approval ticket.")
            state = copy_state(state, approval_status=decision, status="RUNNING")
            state = record_event(state, "human_approval_gate", f"Operator decision: {decision}.", "decision")
            version = self.store.save(state, version)
            next_node: Node = "tool_executor_node" if decision == "APPROVED" else "abort_recovery_node"
            return self._drive(state, version, next_node)

    def _drive(
        self, state: AgentState, version: int, node: Node
    ) -> tuple[AgentState, int]:
        """Execute only known graph nodes; bounded even if a routing bug is introduced."""
        nodes = {
            "agent_node": self._agent_node,
            "circuit_breaker_node": self._circuit_breaker_node,
            "human_approval_gate": self._human_approval_gate,
            "tool_executor_node": self._tool_executor_node,
            "abort_recovery_node": self._abort_recovery_node,
            "error_node": self._error_node,
        }
        for _ in range(4 * self.max_iterations + 10):
            next_state, next_node = nodes[node](state)
            state = next_state
            version = self.store.save(state, version)
            if next_node is None:
                return state, version
            node = next_node
        return self._fail(state, version, "Internal graph transition cap reached.")

    def _agent_node(self, state: AgentState) -> tuple[AgentState, Node | None]:
        if state["iteration_count"] >= self.max_iterations:
            state = record_event(
                state, "agent_node", f"Hard cycle cap reached: {self.max_iterations}/{self.max_iterations}.", "error"
            )
            return copy_state(state, error="Maximum reasoning cycles exceeded."), "error_node"
        cycles = state["iteration_count"] + 1
        state = record_event(state, "agent_node", f"Reasoning cycle {cycles}/{self.max_iterations}.")
        state = copy_state(state, iteration_count=cycles)
        if state["plan_cursor"] >= len(state["plan"]):
            response = self._summarize(state)
            state = add_message(state, "assistant", response)
            state = copy_state(state, status="COMPLETED", last_response=response, pending_tool=None)
            state = record_event(state, "END", "All requested actions resolved.", "success")
            return state, None
        selected = deepcopy(state["plan"][state["plan_cursor"]])
        if not condition_passes(selected["condition"], state["last_metric"]):
            state = record_event(
                state, "agent_node", f"Skipped {selected['name']}: threshold condition was not met.", "info"
            )
            skipped = deepcopy(state["tool_results"])
            skipped.append({"tool": selected["name"], "ok": True, "skipped": True, "reason": "Condition not met"})
            state = copy_state(state, plan_cursor=state["plan_cursor"] + 1, tool_results=skipped)
            return state, "agent_node"
        # Optional real, local LLM tool-binding step. The model is advisory:
        # it cannot add actions or override deterministic control flow.
        if os.getenv("AGENT_PLANNER", "deterministic").lower() == "ollama":
            goals = [m["content"] for m in state["messages"] if m["role"] == "user"]
            try:
                _matched, note = propose_next_tool(
                    goals[-1] if goals else "", selected, state["tool_results"]
                )
                state = record_event(state, "agent_node", note)
            except LocalModelError as exc:
                state = record_event(state, "agent_node", str(exc))
        state = copy_state(state, pending_tool=selected)
        return state, "circuit_breaker_node"

    def _circuit_breaker_node(self, state: AgentState) -> tuple[AgentState, Node]:
        call = state["pending_tool"]
        if call is None:
            return copy_state(state, error="No pending tool to inspect."), "error_node"
        allowed, reason, fingerprint = assess_cycle(
            state["iteration_count"] - 1,
            state["executed_tool_hashes"],
            call["name"],
            call["arguments"],
            max_iterations=self.max_iterations,
        )
        state = record_event(state, "circuit_breaker_node", f"Checked SHA-256 payload {fingerprint[:12]}...")
        if not allowed:
            state = record_event(state, "circuit_breaker_node", reason or "Circuit breaker trip", "error")
            return copy_state(state, error=reason), "error_node"
        try:
            validate_tool(call["name"], call["arguments"])
        except ToolExecutionError as exc:
            return copy_state(state, error=str(exc)), "error_node"
        hashes = state["executed_tool_hashes"] + [fingerprint]
        state = copy_state(state, executed_tool_hashes=hashes[-self.max_iterations:])
        if REGISTRY[call["name"]].is_sensitive:
            return state, "human_approval_gate"
        return state, "tool_executor_node"

    def _human_approval_gate(self, state: AgentState) -> tuple[AgentState, None]:
        call = state["pending_tool"]
        if call is None:
            return self._error_node(copy_state(state, error="Missing sensitive tool"))
        ticket = {
            "ticket_id": str(uuid4()),
            "run_id": state["run_id"],
            "tool_name": call["name"],
            "arguments": deepcopy(call["arguments"]),
            "payload_hash": tool_fingerprint(call["name"], call["arguments"]),
            "risk": REGISTRY[call["name"]].risk,
            "submitted_at": utc_now(),
        }
        state = copy_state(state, status="PAUSED", pending_approval=ticket, approval_status=None)
        state = record_event(state, "human_approval_gate", "Sensitive action paused; durable approval checkpoint saved.", "pause")
        return state, None

    def _tool_executor_node(self, state: AgentState) -> tuple[AgentState, Node]:
        call = state["pending_tool"]
        if call is None:
            return copy_state(state, error="Executor has no tool call"), "error_node"
        spec = REGISTRY.get(call["name"])
        if spec is None:
            return copy_state(state, error="Unknown tool rejected"), "error_node"
        fingerprint = tool_fingerprint(call["name"], call["arguments"])
        authorized = not spec.is_sensitive
        idempotency_key = None
        if spec.is_sensitive:
            ticket = state["pending_approval"]
            authorized = bool(
                ticket is not None
                and state["approval_status"] == "APPROVED"
                and state["status"] == "RUNNING"
                and ticket["run_id"] == state["run_id"]
                and ticket["payload_hash"] == fingerprint
                and ticket["tool_name"] == call["name"]
                and ticket["arguments"] == call["arguments"]
            )
            if not authorized:
                return copy_state(state, error="Sensitive tool blocked: missing matching approval."), "error_node"
            # Stable per-run operation ID means retries/resumes cannot repeat a mutation.
            idempotency_key = tool_fingerprint(state["run_id"], {"payload_hash": fingerprint})
        state = record_event(state, "tool_executor_node", f"Executing {call['name']} ({'SIMULATED' if spec.is_sensitive else 'READ-ONLY'}).")
        try:
            result = execute_tool(
                call["name"], call["arguments"], store=self.store, thread_id=state["thread_id"],
                idempotency_key=idempotency_key, authorized=authorized,
            )
        except (ToolExecutionError, PermissionError) as exc:
            if call["retry_on_error_demo"]:
                state = record_event(state, "tool_executor_node", f"Injected tool error: {exc}; retry is queued.", "error")
                state = add_message(state, "tool", f"ERROR: {exc}", tool_name=call["name"])
                # Keep plan cursor: reselect exactly the same call; SHA-256 trips next cycle.
                return state, "agent_node"
            state = record_event(state, "tool_executor_node", f"Tool failure: {exc}", "error")
            return copy_state(state, error=f"Tool failed: {exc}"), "error_node"
        results = deepcopy(state["tool_results"])
        results.append({"tool": call["name"], **result})
        metrics = deepcopy(state["last_metric"])
        if call["name"] == "query_service_metrics":
            metrics[result["service_name"]] = float(result["error_rate_pct"])
        state = copy_state(
            state, tool_results=results, last_metric=metrics, plan_cursor=state["plan_cursor"] + 1,
            pending_tool=None, pending_approval=None, approval_status=None,
        )
        state = add_message(state, "tool", str(result), tool_name=call["name"])
        state = record_event(state, "tool_executor_node", f"{call['name']} completed successfully.", "success")
        return state, "agent_node"

    def _abort_recovery_node(self, state: AgentState) -> tuple[AgentState, None]:
        action = state["pending_approval"]
        label = action["tool_name"] if action is not None else "Sensitive action"
        response = (
            f"Operator rejected {label}. No unapproved sensitive action was executed. "
            "This request has been safely aborted; no further actions will be attempted."
        )
        state = record_event(state, "abort_recovery_node", "Rejected action and canceled remaining plan.", "decision")
        state = add_message(state, "tool", f"HUMAN_REJECTED: {label}", tool_name=label)
        state = add_message(state, "assistant", response)
        state = copy_state(
            state, status="REJECTED", last_response=response, plan=[], plan_cursor=0,
            pending_tool=None, pending_approval=None, approval_status="REJECTED",
        )
        state = record_event(state, "END", "Aborted without executing the rejected action.", "success")
        return state, None

    def _error_node(self, state: AgentState) -> tuple[AgentState, None]:
        reason = state["error"] or "Unknown graph error"
        response = f"Execution stopped safely. {reason} No unapproved sensitive action was performed."
        state = record_event(state, "error_node", reason, "error")
        state = add_message(state, "assistant", response)
        state = copy_state(
            state, status="ERROR", last_response=response,
            pending_tool=None, pending_approval=None, approval_status=None,
        )
        state = record_event(state, "END", "Circuit/error stop finalized.", "error")
        return state, None

    def _fail(self, state: AgentState, version: int, reason: str) -> tuple[AgentState, int]:
        state, _ = self._error_node(copy_state(state, error=reason))
        return state, self.store.save(state, version)

    @staticmethod
    def _summarize(state: AgentState) -> str:
        parts: list[str] = []
        for result in state["tool_results"]:
            if result.get("skipped"):
                parts.append(f"Skipped {result['tool']}: error-rate threshold was not exceeded.")
            elif result["tool"] == "query_service_metrics":
                parts.append(
                    f"{result['service_name']} error rate is {result['error_rate_pct']}% "
                    f"(p95 latency {result['latency_ms_p95']} ms)."
                )
            elif result["tool"] == "check_billing_balance":
                parts.append(f"Account {result['account_id']} has a DEMO balance of ${result['balance_usd']:,.2f}.")
            elif result["tool"] == "scale_kubernetes_cluster":
                args = result["arguments"]
                parts.append(f"SIMULATED: scaled {args['service_name']} to {args['replica_count']} replicas; ticket {result['operation_id']}.")
            elif result["tool"] == "execute_wire_transfer":
                args = result["arguments"]
                parts.append(f"SIMULATED: recorded ${args['amount_usd']:,.2f} to {args['recipient_id']}; ticket {result['operation_id']}.")
        return " ".join(parts) + " All mutations are simulated; no real infrastructure or money was affected."
