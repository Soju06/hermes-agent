"""User-visible heartbeats for long tool executions and stalled model waits."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from agent.tool_executor import _long_wait_heartbeat, _wait_heartbeat_seconds


class _Agent:
    def __init__(self):
        self.status_callback = MagicMock()
        self.statuses = []

    def _emit_status(self, message):
        self.statuses.append(message)


class TestWaitHeartbeatSeconds:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("HERMES_WAIT_HEARTBEAT_SECONDS", raising=False)
        assert _wait_heartbeat_seconds() == 90.0

    def test_env_override_and_disable(self, monkeypatch):
        monkeypatch.setenv("HERMES_WAIT_HEARTBEAT_SECONDS", "5")
        assert _wait_heartbeat_seconds() == 5.0
        monkeypatch.setenv("HERMES_WAIT_HEARTBEAT_SECONDS", "0")
        assert _wait_heartbeat_seconds() == 0.0

    def test_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("HERMES_WAIT_HEARTBEAT_SECONDS", "soon")
        assert _wait_heartbeat_seconds() == 90.0


class TestLongWaitHeartbeat:
    def test_beats_during_slow_tool(self, monkeypatch):
        monkeypatch.setenv("HERMES_WAIT_HEARTBEAT_SECONDS", "0.05")
        agent = _Agent()
        with _long_wait_heartbeat(agent, "process", {"action": "wait"}):
            time.sleep(0.25)
        assert len(agent.statuses) >= 2
        assert "still running" in agent.statuses[0]
        assert "process" in agent.statuses[0]

    def test_silent_for_fast_tool(self, monkeypatch):
        monkeypatch.setenv("HERMES_WAIT_HEARTBEAT_SECONDS", "1")
        agent = _Agent()
        with _long_wait_heartbeat(agent, "terminal", {"command": "true"}):
            time.sleep(0.02)
        assert agent.statuses == []

    def test_disabled_by_env_zero(self, monkeypatch):
        monkeypatch.setenv("HERMES_WAIT_HEARTBEAT_SECONDS", "0")
        agent = _Agent()
        with _long_wait_heartbeat(agent, "process", {}):
            time.sleep(0.1)
        assert agent.statuses == []

    def test_no_status_callback_no_thread(self, monkeypatch):
        monkeypatch.setenv("HERMES_WAIT_HEARTBEAT_SECONDS", "0.05")
        agent = _Agent()
        agent.status_callback = None
        before = threading.active_count()
        with _long_wait_heartbeat(agent, "process", {}):
            time.sleep(0.12)
        assert agent.statuses == []

    def test_beat_stops_after_exit(self, monkeypatch):
        monkeypatch.setenv("HERMES_WAIT_HEARTBEAT_SECONDS", "0.05")
        agent = _Agent()
        with _long_wait_heartbeat(agent, "process", {}):
            time.sleep(0.06)
        count = len(agent.statuses)
        time.sleep(0.15)
        assert len(agent.statuses) == count, "heartbeat must stop when the tool returns"
