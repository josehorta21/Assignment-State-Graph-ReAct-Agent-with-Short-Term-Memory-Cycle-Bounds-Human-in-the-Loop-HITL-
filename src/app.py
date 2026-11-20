"""Local-only FastAPI dashboard and graph controller."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .checkpoint import SQLiteCheckpointer, VersionConflict
from .graph import AgentGraph, GraphInputError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = os.getenv("AGENT_DB_PATH", str(PROJECT_ROOT / "data" / "checkpoints.db"))
STORE = SQLiteCheckpointer(DB_PATH)
GRAPH = AgentGraph(STORE, max_iterations=int(os.getenv("AGENT_MAX_ITERATIONS", "6")))
app = FastAPI(
    title="StateGuard | Deterministic Agent Control",
    summary="SQLite-backed directed state graph with HITL and loop containment",
    description="Educational simulator; no real cloud changes, bank access, or financial transactions.",
    version="1.0.0",
)
app.mount("/assets", StaticFiles(directory=str(PROJECT_ROOT / "assets")), name="assets")

THREAD_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class PromptRequest(BaseModel):
    thread_id: str = Field(min_length=8, max_length=64)
    prompt: str = Field(min_length=1, max_length=2000)
    inject_failure: bool = False


class DecisionRequest(BaseModel):
    thread_id: str = Field(min_length=8, max_length=64)
    ticket_id: str = Field(min_length=10, max_length=80)
    decision: Literal["APPROVED", "REJECTED"]


def validate_thread(thread_id: str) -> None:
    if not THREAD_PATTERN.fullmatch(thread_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format.")


def get_state_or_404(thread_id: str) -> dict:
    validate_thread(thread_id)
    try:
        state, version = GRAPH.get_session(thread_id)
    except GraphInputError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"state": state, "version": version, "simulation_mode": True, "graph_engine": "Custom Directed State Graph"}


@app.get("/", include_in_schema=False)
def homepage() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "assets" / "index.html")


@app.get("/healthz")
def health() -> dict:
    return {"ok": True, "engine": "Custom Directed State Graph", "simulation": True}


@app.post("/api/sessions", status_code=201)
def create_session() -> dict:
    state = GRAPH.create_session()
    return get_state_or_404(state["thread_id"])


@app.get("/api/state/{thread_id}")
def get_state(thread_id: str) -> dict:
    return get_state_or_404(thread_id)


@app.post("/api/submit")
def submit(payload: PromptRequest) -> dict:
    validate_thread(payload.thread_id)
    try:
        GRAPH.submit(payload.thread_id, payload.prompt, inject_failure=payload.inject_failure)
    except GraphInputError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VersionConflict as exc:
        raise HTTPException(status_code=409, detail="Another request updated the session; refresh and retry.") from exc
    return get_state_or_404(payload.thread_id)


@app.post("/api/decide")
def decide(payload: DecisionRequest) -> dict:
    validate_thread(payload.thread_id)
    try:
        GRAPH.decide(payload.thread_id, payload.ticket_id, payload.decision)
    except GraphInputError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VersionConflict as exc:
        raise HTTPException(status_code=409, detail="Concurrent approval; refresh state before retrying.") from exc
    return get_state_or_404(payload.thread_id)


@app.websocket("/ws/{thread_id}")
async def state_updates(websocket: WebSocket, thread_id: str) -> None:
    if not THREAD_PATTERN.fullmatch(thread_id):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    last_version = -1
    try:
        while True:
            try:
                snapshot = get_state_or_404(thread_id)
            except HTTPException:
                await websocket.close(code=1008)
                return
            if snapshot["version"] != last_version:
                await websocket.send_json(snapshot)
                last_version = snapshot["version"]
            await asyncio.sleep(0.25)
    except (WebSocketDisconnect, RuntimeError):
        return
