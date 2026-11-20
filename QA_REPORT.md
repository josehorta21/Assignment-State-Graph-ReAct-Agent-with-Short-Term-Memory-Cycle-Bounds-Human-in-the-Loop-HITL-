# QA Verification Report

**System:** StateGuard - Custom Directed State Graph  
**Environment:** Python 3.13.5 (application target: Python 3.11+)  
**Executed command:** `python -m pytest -v`  
**Result:** **27 passed** (local sandbox execution)

## Test matrix

| Group | Coverage | Verified |
| --- | --- | --- |
| Safe tool flows | Autonomous execution, conditional threshold, multi-turn memory, isolated threads | PASS |
| Durable checkpoints | SQLite state reload and paused-state recovery across graph instances | PASS |
| Loop controls | Canonical SHA-256, different payloads, duplicate detection, maximum cycle bound | PASS |
| Error containment | Simulated 503, repeated call triggers terminal circuit-breaker state | PASS |
| HITL authorization | Sensitive pause, approval, rejection, stale ticket, replay, direct execution denial | PASS |
| Financial idempotency | Same operation key records only one simulated ledger entry | PASS |
| Interface | Dashboard/health endpoints, API approval workflow, session validation, WebSocket snapshot | PASS |
| Optional local-model validation | Matching tool accepted; argument-altering proposal rejected | PASS |

The automated tests execute against a disposable SQLite database or isolated test sessions. They do not contact real banking, cloud, or paid LLM APIs.

## Additional verification

The FastAPI application was started with:

```bash
uvicorn src.app:app --host 127.0.0.1 --port 8765
```

The `/healthz` endpoint returned HTTP 200 with `simulation=true`. Docker installation and graphical-browser screenshots were unavailable in the generation environment; the Docker image/Compose definition and CSS/JS UI should be verified on the presenter's own machine before recording.

## Reproduction

```bash
uv sync --extra dev
uv run pytest -v
```

Python 3.11 is the pinned project version, and tests were run here with Python 3.13.5. No claims of container build verification or real LLM execution are made.
