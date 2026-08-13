from __future__ import annotations

from types import SimpleNamespace


def test_compression_split_republishes_runtime_state_for_new_session(monkeypatch, tmp_path):
    from agent.conversation_compression import compress_context

    events = []

    def fake_invoke_hook(hook_name, **kwargs):
        events.append((hook_name, kwargs))
        return []

    monkeypatch.setattr("hermes_cli.lifecycle.invoke_hook", fake_invoke_hook)

    class Compressor:
        compression_count = 1
        _last_compress_aborted = False
        _last_summary_error = None
        _last_aux_model_failure_model = None
        _last_aux_model_failure_error = None
        last_compression_rough_tokens = 0
        last_prompt_tokens = 0
        last_completion_tokens = 0
        awaiting_real_usage_after_compression = False

        def compress(self, messages, **kwargs):
            return [{"role": "user", "content": "[summary]"}]

    class SessionDB:
        def try_acquire_compression_lock(self, session_id, holder, ttl_seconds=None):
            return True

        def refresh_compression_lock(self, session_id, holder, ttl_seconds=None):
            return True

        def release_compression_lock(self, session_id, holder):
            pass

        def get_session_title(self, session_id):
            return None

        def end_session(self, session_id, reason):
            pass

        def create_session(self, **kwargs):
            self.created = kwargs

        def update_system_prompt(self, session_id, system_prompt):
            pass

        def publish_compression_child(self, **kwargs):
            self.created = kwargs

    agent = SimpleNamespace(
        _compression_feasibility_checked=True,
        compression_in_place=False,
        session_id="old-session",
        model="gpt-5.5",
        provider="codex-nekos",
        base_url="https://codex.nekos.me/v1",
        api_mode="codex_responses",
        reasoning_config={"enabled": True, "effort": "high"},
        platform="discord",
        _emit_status=lambda *a, **k: None,
        _emit_warning=lambda *a, **k: None,
        _memory_manager=None,
        context_compressor=Compressor(),
        _todo_store=SimpleNamespace(format_for_injection=lambda: ""),
        _invalidate_system_prompt=lambda: None,
        _build_system_prompt=lambda system_message: "new-system",
        _cached_system_prompt=None,
        _session_db=SessionDB(),
        _session_init_model_config={},
        _session_db_created=True,
        _last_flushed_db_idx=0,
        _vprint=lambda *a, **k: None,
        log_prefix="",
        tools=None,
    )
    agent.commit_memory_session = lambda messages: None

    compressed, new_prompt = compress_context(
        agent,
        [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "world"}],
        "system",
        task_id="old-session",
    )

    assert compressed == [{"role": "user", "content": "[summary]"}]
    assert new_prompt == "new-system"
    assert agent.session_id != "old-session"

    runtime_events = [kwargs for hook, kwargs in events if hook == "runtime_state"]
    assert runtime_events, "compression split should emit runtime_state before later tool gates fire"
    event = runtime_events[0]
    assert event["session_id"] == agent.session_id
    assert event["task_id"] == "old-session"
    assert event["state"] == {
        "session_id": agent.session_id,
        "task_id": "old-session",
        "model": "gpt-5.5",
        "provider": "codex-nekos",
        "base_url": "https://codex.nekos.me/v1",
        "api_mode": "codex_responses",
        "platform": "discord",
        "reasoning_effort": "high",
        "parent_session_id": "old-session",
        "boundary_reason": "compression",
    }
    assert "api_key" not in event["state"]
