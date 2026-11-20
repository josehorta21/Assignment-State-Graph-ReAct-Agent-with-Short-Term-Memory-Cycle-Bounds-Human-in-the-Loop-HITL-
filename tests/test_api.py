from __future__ import annotations

from fastapi.testclient import TestClient

from src.app import app


client = TestClient(app)


def test_dashboard_and_health_are_served():
    assert client.get("/").status_code == 200
    assert "StateGuard" in client.get("/").text
    assert client.get("/healthz").json()["simulation"] is True


def test_api_approval_lifecycle():
    created = client.post("/api/sessions", json={})
    assert created.status_code == 201
    thread_id = created.json()["state"]["thread_id"]
    submitted = client.post("/api/submit", json={
        "thread_id": thread_id, "prompt": "Transfer $1500 to V-8812"
    })
    assert submitted.status_code == 200
    assert submitted.json()["state"]["status"] == "PAUSED"
    ticket = submitted.json()["state"]["pending_approval"]["ticket_id"]
    done = client.post("/api/decide", json={
        "thread_id": thread_id, "ticket_id": ticket, "decision": "APPROVED"
    })
    assert done.status_code == 200
    assert done.json()["state"]["status"] == "COMPLETED"


def test_api_rejects_bad_session_ids():
    assert client.get("/api/state/invalid!").status_code == 400


def test_websocket_streams_checkpoint_snapshot():
    created = client.post("/api/sessions", json={}).json()
    thread_id = created["state"]["thread_id"]
    with client.websocket_connect(f"/ws/{thread_id}") as websocket:
        snapshot = websocket.receive_json()
        assert snapshot["state"]["thread_id"] == thread_id
        assert snapshot["version"] >= 1
