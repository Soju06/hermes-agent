"""An agent ``model_switch`` (agent/runtime_control.py) on a LOCAL session commits through its owner.

A local session (classic CLI, TUI, Desktop served by the gateway) runs its frozen launch policy:
``_resolve_session_agent_runtime`` answers from the policy before any messaging override is read,
so the messaging override slot and the SessionEntry ``runtime_*`` fields never reach its next turn.
The switch lands where a typed ``/model`` (``/reasoning``) on that session lands instead: the
owner's canonical policy mutation, committed by the drain at the next turn boundary and durable
across an owner restart. The running turn keeps the live agent's in-place switch.
"""
from types import SimpleNamespace


def commit_local_runtime_switch(runner, source, model_override, reasoning_config) -> bool:
    """Commit a session-scoped runtime switch of the LOCAL session *source* belongs to; False for
    any other session (the caller keeps the messaging override slot). Blocking SQLite: called from
    the agent's worker thread, never the event loop."""
    from gateway.session_setting_commands import local_authority_live
    authority, live = local_authority_live(runner, source)
    if live is None:
        return False
    from gateway.run_agent_cache import GatewayAgentCacheMixin
    effort = GatewayAgentCacheMixin._reasoning_effort_from_config(reasoning_config) or ''
    if model_override:
        from gateway.session_setting_commands import defer_local_model_pick
        # The route's resolved target, never an alias: runtime_control refuses a switch the shared
        # resolver landed anywhere but the declared provider/model.
        picked = SimpleNamespace(resolved_via_alias='', new_model=model_override.get('model') or '',
                                 target_provider=model_override.get('provider') or '')
        # A level switched with the model lands in the same commit (``/model X --reasoning <level>``).
        defer_local_model_pick(authority, live, picked, effort)
    elif effort:
        # A level alone: what ``/reasoning <level>`` on a busy local session does (the handler's
        # in-memory level for the next turn, then ``apply_pending_reasoning`` at the boundary).
        from hermes_state_local import local_receipt
        runner._set_session_reasoning_override(live.route, reasoning_config)
        live.pending_reasoning = (local_receipt(authority.db, live.source.chat_id)['principal_id'], effort)
    return True
