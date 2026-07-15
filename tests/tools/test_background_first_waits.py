"""Background-first wait escalation: chained foreground waits arm notify_on_complete."""

from __future__ import annotations

import time

import pytest

from tools.process_registry import ProcessRegistry


@pytest.fixture()
def registry():
    return ProcessRegistry()


class TestWaitEscalation:
    def test_second_consecutive_timeout_arms_notify(self, registry, monkeypatch):
        monkeypatch.delenv("HERMES_PROCESS_WAIT_CAP", raising=False)
        sess = registry.spawn_local("sleep 30", cwd=None)
        try:
            r1 = registry.wait(sess.id, timeout=1)
            assert r1["status"] == "timeout"
            assert "notify_on_complete" not in r1
            assert sess.notify_on_complete is False

            r2 = registry.wait(sess.id, timeout=1)
            assert r2["status"] == "timeout"
            assert r2.get("notify_on_complete") is True
            assert "end your turn" in r2["timeout_note"].lower()
            assert sess.notify_on_complete is True
        finally:
            registry.kill_process(sess.id)

    def test_streak_resets_on_exit(self, registry, monkeypatch):
        monkeypatch.delenv("HERMES_PROCESS_WAIT_CAP", raising=False)
        sess = registry.spawn_local("sleep 0.2", cwd=None)
        result = registry.wait(sess.id, timeout=10)
        assert result["status"] == "exited"
        assert getattr(registry, "_wait_timeout_streaks", {}).get(sess.id, 0) == 0

    def test_cap_zero_disables(self, registry, monkeypatch):
        monkeypatch.setenv("HERMES_PROCESS_WAIT_CAP", "0")
        sess = registry.spawn_local("sleep 30", cwd=None)
        try:
            registry.wait(sess.id, timeout=1)
            r2 = registry.wait(sess.id, timeout=1)
            assert r2["status"] == "timeout"
            assert "notify_on_complete" not in r2
            assert sess.notify_on_complete is False
        finally:
            registry.kill_process(sess.id)

    def test_cap_two_allows_two_quiet_waits(self, registry, monkeypatch):
        monkeypatch.setenv("HERMES_PROCESS_WAIT_CAP", "2")
        sess = registry.spawn_local("sleep 30", cwd=None)
        try:
            registry.wait(sess.id, timeout=1)
            r2 = registry.wait(sess.id, timeout=1)
            assert "notify_on_complete" not in r2
            r3 = registry.wait(sess.id, timeout=1)
            assert r3.get("notify_on_complete") is True
        finally:
            registry.kill_process(sess.id)
