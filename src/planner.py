"""Auditable, deterministic demo planner; no cloud model, key, or paid API required.

This is a constrained ReAct-like tool policy, not a generative LLM. The graph
handles tool observations and chooses the next action after each result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .state import ToolCall


@dataclass
class PlanResult:
    calls: list[ToolCall] = field(default_factory=list)
    immediate_response: str = ""
    fact_updates: dict[str, str] = field(default_factory=dict)


def tool_call(
    name: str,
    arguments: dict[str, Any],
    *,
    condition: dict[str, Any] | None = None,
    retry_on_error_demo: bool = False,
) -> ToolCall:
    return {
        "name": name,
        "arguments": arguments,
        "condition": condition,
        "retry_on_error_demo": retry_on_error_demo,
    }


def _service_name(text: str) -> str | None:
    match = re.search(r"\b([a-z][a-z0-9-]*(?:-service|-api))\b", text, re.IGNORECASE)
    return match.group(1).lower() if match else None


def _recipient_id(text: str) -> str | None:
    match = re.search(r"\b(?:vendor|recipient)(?:\s+id)?\s*[:#-]?\s*([A-Z]-\d{3,})\b", text, re.I)
    if match:
        return match.group(1).upper()
    match = re.search(r"\b(V-\d{3,})\b", text, re.I)
    return match.group(1).upper() if match else None


def _amount(text: str) -> float | None:
    match = re.search(r"\$\s*([\d,]+(?:\.\d{1,2})?)", text)
    if not match:
        match = re.search(r"\btransfer\s+([\d,]+(?:\.\d{1,2})?)\b", text, re.I)
    return float(match.group(1).replace(",", "")) if match else None


def build_plan(text: str, facts: dict[str, str], *, inject_failure: bool = False) -> PlanResult:
    """Translate supported demo intents into explicit tool calls without inferred IDs.

    An optional failure-injection flag forces a repeated failing read-only call.
    Sensitive actions are NEVER invoked here; authorization lives in the graph.
    """
    if inject_failure:
        return PlanResult(calls=[tool_call(
            "query_service_metrics",
            {"service_name": "unstable-service", "simulate_failure": True},
            retry_on_error_demo=True,
        )])

    prompt = text.strip()
    lower = prompt.lower()
    name_set = re.search(r"\b(?:my name is|call me)\s+([a-zA-Z][a-zA-Z'-]{1,29})\b", prompt, re.I)
    if name_set:
        name = name_set.group(1).title()
        return PlanResult(immediate_response=f"I will remember your name as {name} for this session.", fact_updates={"user_name": name})
    if re.search(r"\b(?:what(?:'s| is) my name|do you remember my name)\b", lower):
        name = facts.get("user_name")
        return PlanResult(immediate_response=(f"Your name is {name}." if name else "I do not have your name in this session yet."))

    calls: list[ToolCall] = []
    service = _service_name(prompt)
    wants_metrics = any(word in lower for word in ("error rate", "metric", "latency", "health"))
    wants_scale = bool(re.search(r"\bscal(?:e|ing)\b", lower))
    wants_transfer = bool(re.search(r"\b(?:transfer|wire)\b", lower))
    wants_balance = bool(re.search(r"\bbalance\b", lower))
    conditional = bool(re.search(r"\b(?:if|when)\b", lower)) and "over" in lower
    threshold_match = re.search(r"\b(?:over|above|exceeds?|greater than)\s*(\d+(?:\.\d+)?)\s*%", lower)
    threshold = float(threshold_match.group(1)) if threshold_match else None
    condition = (
        {"service_name": service, "error_rate_gt": threshold}
        if conditional and service and threshold is not None
        else None
    )

    if conditional and (wants_scale or wants_transfer) and condition is None:
        return PlanResult(immediate_response="I need an explicit service name and a numeric error-rate threshold before executing conditional actions.")

    if wants_metrics or (conditional and wants_scale):
        if not service:
            return PlanResult(immediate_response="Please specify a service name, such as auth-service, for the metrics query.")
        calls.append(tool_call("query_service_metrics", {"service_name": service}))

    if wants_balance:
        account_match = re.search(r"\b(DEMO-\d{3,}|ACC-\d{3,})\b", prompt, re.I)
        if not account_match:
            return PlanResult(immediate_response="Please provide a demo account ID, for example DEMO-100.")
        calls.append(tool_call("check_billing_balance", {"account_id": account_match.group(1).upper()}))

    if wants_scale:
        replica_match = re.search(r"\b(?:replicas?\s*(?:to|=)?|scale\s+(?:to)?\s*)\s*(\d+)\b", lower)
        if service is None or replica_match is None:
            return PlanResult(immediate_response="Scaling requires an explicit service name and replica count, e.g. 'scale auth-service replicas to 10'.")
        replicas = int(replica_match.group(1))
        calls.append(tool_call("scale_kubernetes_cluster", {
            "service_name": service, "replica_count": replicas,
        }, condition=condition))

    if wants_transfer:
        amount = _amount(prompt)
        recipient = _recipient_id(prompt)
        if recipient is None or amount is None:
            return PlanResult(immediate_response="A simulated wire transfer needs an explicit recipient ID (e.g. V-8812) and USD amount (e.g. $1500).")
        calls.append(tool_call("execute_wire_transfer", {
            "recipient_id": recipient, "amount_usd": amount,
        }, condition=condition))

    if not calls:
        return PlanResult(immediate_response=(
            "This offline demonstration supports service metrics, demo billing balances, "
            "simulated Kubernetes scaling, simulated transfers, and remembering a name. "
            "Select an example prompt to try the state graph."
        ))
    return PlanResult(calls=calls)


def condition_passes(condition: dict[str, Any] | None, last_metric: dict[str, float]) -> bool:
    if condition is None:
        return True
    service = condition.get("service_name")
    rate = last_metric.get(str(service))
    return rate is not None and rate > condition["error_rate_gt"]
