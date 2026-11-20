"""ARCHIVED AI-ASSISTED SKETCH — DELIBERATELY INSECURE; DO NOT IMPORT.

This file preserves early examples that were rejected during architectural
review. It is never used by src/app.py or src/graph.py.
"""

import hashlib

GLOBAL_THREAD_ID = "default-thread"  # FLAW: cross-user session contamination


def flawed_tool_hash(tool_name, args):
    # FLAW: formatting/argument order changes the fingerprint.
    return hashlib.sha256(str({"tool": tool_name, "arguments": args}).encode()).hexdigest()


def flawed_append_message(state, content):
    # FLAW: in-place shared list mutation can bypass reducer/checkpoint semantics.
    state["messages"].append({"role": "tool", "content": content})
    return state


def flawed_rejection_route(state):
    # FLAW: the rejected call remains pending; the next agent cycle may repeat it.
    state["approval_status"] = "REJECTED"
    return "agent_node"


def flawed_execute_before_approval(tool_function, args):
    # FLAW: no independent authorization check at the execution boundary.
    return tool_function(**args)
