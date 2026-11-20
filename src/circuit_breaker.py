"""Deterministic loop control; all decisions are enforced outside the agent."""

from __future__ import annotations

import hashlib
import json
from typing import Any


MAX_ITERATIONS = 6
HASH_WINDOW = 6


def tool_fingerprint(name: str, arguments: dict[str, Any]) -> str:
    """Canonicalize the tool name and JSON arguments before SHA-256 hashing."""
    canonical = json.dumps(
        {"tool": name, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assess_cycle(
    iteration_count: int,
    recent_hashes: list[str],
    tool_name: str,
    arguments: dict[str, Any],
    max_iterations: int = MAX_ITERATIONS,
) -> tuple[bool, str | None, str]:
    """Return (allowed, stop_reason, fingerprint). Reject before executing anything."""
    fingerprint = tool_fingerprint(tool_name, arguments)
    if iteration_count >= max_iterations:
        return False, f"Cycle budget exceeded: {iteration_count}/{max_iterations} reasoning cycles.", fingerprint
    if fingerprint in recent_hashes[-HASH_WINDOW:]:
        return False, "Duplicate payload circuit breaker tripped: identical tool call in this run.", fingerprint
    return True, None, fingerprint
