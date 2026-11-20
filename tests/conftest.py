from __future__ import annotations

import pytest

from src.checkpoint import SQLiteCheckpointer
from src.graph import AgentGraph


@pytest.fixture
def graph(tmp_path):
    return AgentGraph(SQLiteCheckpointer(tmp_path / "checkpoints.db"))


@pytest.fixture
def session(graph):
    return graph.create_session()["thread_id"]


SAMPLE_INCIDENT = (
    "Check the current error rate on the auth-service. If it's over 5%, "
    "scale its replicas to 10 and transfer $1,500 to the contracted "
    "on-call engineering firm (Vendor ID: V-8812) for emergency mitigation."
)
