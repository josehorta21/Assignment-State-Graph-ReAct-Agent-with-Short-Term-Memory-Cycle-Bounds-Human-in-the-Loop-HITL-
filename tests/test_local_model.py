from __future__ import annotations

import json
from io import BytesIO

from src.local_model import propose_next_tool
from src.planner import tool_call


class FakeResponse:
    def __init__(self, body):
        self.data = BytesIO(json.dumps(body).encode())

    def __enter__(self):
        return self.data

    def __exit__(self, exc_type, exc_value, traceback):
        self.data.close()


def test_local_model_accepts_exact_tool_call(monkeypatch):
    from src import local_model

    def fake_urlopen(request, timeout):
        return FakeResponse({"message": {"tool_calls": [{"function": {
            "name": "check_billing_balance", "arguments": {"account_id": "DEMO-100"}
        }}]}})

    monkeypatch.setattr(local_model, "urlopen", fake_urlopen)
    matched, note = propose_next_tool(
        "Check DEMO-100 balance", tool_call("check_billing_balance", {"account_id": "DEMO-100"}), []
    )
    assert matched and "matching" in note


def test_local_model_cannot_change_sensitive_tool_arguments(monkeypatch):
    from src import local_model

    def fake_urlopen(request, timeout):
        return FakeResponse({"message": {"tool_calls": [{"function": {
            "name": "execute_wire_transfer", "arguments": {"recipient_id": "V-9999", "amount_usd": 1500.0}
        }}]}})

    monkeypatch.setattr(local_model, "urlopen", fake_urlopen)
    matched, note = propose_next_tool(
        "Transfer $1500 to V-8812",
        tool_call("execute_wire_transfer", {"recipient_id": "V-8812", "amount_usd": 1500.0}),
        [],
    )
    assert not matched and "changed tool arguments" in note
