"""Allowed tools. ALL mutations are simulated and require runtime authorization."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .checkpoint import SQLiteCheckpointer


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    is_sensitive: bool
    risk: str


REGISTRY = {
    "query_service_metrics": ToolSpec(
        "query_service_metrics", "Retrieve a simulated service error rate.", False, "LOW"
    ),
    "check_billing_balance": ToolSpec(
        "check_billing_balance", "Read a simulated billing balance.", False, "LOW"
    ),
    "scale_kubernetes_cluster": ToolSpec(
        "scale_kubernetes_cluster", "Simulate a Kubernetes replica mutation.", True, "HIGH - INFRASTRUCTURE"
    ),
    "execute_wire_transfer": ToolSpec(
        "execute_wire_transfer", "Simulate a USD transfer; never moves money.", True, "HIGH - FINANCIAL"
    ),
}

DEMO_METRICS = {
    "auth-service": {"error_rate_pct": 8.2, "latency_ms_p95": 418, "requests_per_min": 1842},
    "payments-api": {"error_rate_pct": 2.1, "latency_ms_p95": 126, "requests_per_min": 990},
    "healthy-service": {"error_rate_pct": 0.3, "latency_ms_p95": 62, "requests_per_min": 430},
}
DEMO_BALANCES = {"DEMO-100": 14250.20, "DEMO-200": 3200.75}


class ToolExecutionError(RuntimeError):
    pass


def validate_tool(name: str, arguments: dict[str, Any]) -> None:
    if name not in REGISTRY:
        raise ToolExecutionError(f"Unknown tool: {name}")
    if name == "query_service_metrics":
        service = arguments.get("service_name")
        if not isinstance(service, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,60}", service):
            raise ToolExecutionError("Invalid service_name")
    elif name == "check_billing_balance":
        account = arguments.get("account_id")
        if not isinstance(account, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", account):
            raise ToolExecutionError("Invalid account_id")
    elif name == "scale_kubernetes_cluster":
        service = arguments.get("service_name")
        replicas = arguments.get("replica_count")
        if not isinstance(service, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,60}", service):
            raise ToolExecutionError("Invalid service_name")
        if not isinstance(replicas, int) or isinstance(replicas, bool) or not 1 <= replicas <= 50:
            raise ToolExecutionError("replica_count must be an integer in the range 1..50")
    elif name == "execute_wire_transfer":
        recipient = arguments.get("recipient_id")
        amount = arguments.get("amount_usd")
        if not isinstance(recipient, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", recipient):
            raise ToolExecutionError("Invalid recipient_id")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount):
            raise ToolExecutionError("amount_usd must be a finite positive amount")
        if not (0 < float(amount) <= 100000):
            raise ToolExecutionError("amount_usd must be in the range (0, 100000]")


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    store: SQLiteCheckpointer,
    thread_id: str,
    idempotency_key: str | None = None,
    authorized: bool = False,
) -> dict[str, Any]:
    """Execute safe mock reads; write only to simulated SQLite ledger after approval."""
    validate_tool(name, arguments)
    if name == "query_service_metrics":
        if arguments.get("simulate_failure"):
            raise ToolExecutionError("Simulated upstream 503: metrics endpoint unavailable")
        service = arguments["service_name"]
        metrics = DEMO_METRICS.get(
            service, {"error_rate_pct": 1.0, "latency_ms_p95": 150, "requests_per_min": 100}
        )
        return {"ok": True, "simulation": True, "service_name": service, **metrics}
    if name == "check_billing_balance":
        account_id = arguments["account_id"]
        return {
            "ok": True,
            "simulation": True,
            "account_id": account_id,
            "currency": "USD",
            "balance_usd": DEMO_BALANCES.get(account_id, 0.0),
        }
    if not authorized or idempotency_key is None:
        raise PermissionError("Sensitive tool requires a matching approved HITL ticket")
    return store.execute_simulated_once(idempotency_key, thread_id, name, arguments)
