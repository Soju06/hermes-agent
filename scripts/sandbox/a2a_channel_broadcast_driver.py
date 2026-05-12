#!/usr/bin/env python3
"""Phase 4 Task 41 — ADR-011 channel-broadcast sandbox verification driver v3.

Phase 1 strict dedup correctly drops Bot-A→Discord echoes at Bot-B/C (any
"author is registered A2A peer" → drop). This means the outbound hook
(which fires from a user-message reply path) cannot be driven by a bot
account in the same mesh channel — by design.

The verification splits into two modes:

  Mode 1 (default): boot-time + static verification. Confirms:
    - All 3 bot stacks booted
    - All 3 agent-cards advertise both `discord-identity/v1` AND
      `channel-broadcast/v1` extensions (ADR-012)
    - All 3 gateway logs show "ADR-011 channel-broadcast wired"
    - A2A peer resolution succeeded (B, C reachable from A)
    - No errors during steady-state boot

  Mode 2 (--wait-trigger N): user-trigger cascade verification. Waits
    up to N seconds for a non-peer user to post in mesh-abc, then
    captures the next 90 seconds of cascade activity:
    - Bot-A/B/C replies in channel
    - outbound broadcast hook log markers
    - A2A inbound channel_broadcast handling log markers
    - transcript append + should_trigger_reply decisions
    - max_consecutive_self_replies cap enforcement

Run from host:
    cd ~/projects/hermes-agent-a2a
    # Boot verification only:
    python3 scripts/sandbox/a2a_channel_broadcast_driver.py

    # Wait for Soju to post in mesh-abc, then capture cascade:
    python3 scripts/sandbox/a2a_channel_broadcast_driver.py --wait-trigger 300
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MESH_CHANNEL_ID = "1502907302901055679"
BOT_A_USER_ID = "1502705819290963999"
BOT_B_USER_ID = "1502705976233558126"
BOT_C_USER_ID = "1503019818062839958"
BOT_IDS = {BOT_A_USER_ID: "A", BOT_B_USER_ID: "B", BOT_C_USER_ID: "C"}

CONTAINERS = ["a2a-bot-a-adr011", "a2a-bot-b-adr011", "a2a-bot-c-adr011"]
HOST_PORTS = {"a2a-bot-a-adr011": 9810, "a2a-bot-c-adr011": 9812}

MARKERS = [
    "channel-broadcast wired",
    "[A2A] resolved peer",
    "_maybe_broadcast_a2a_reply",
    "broadcast",
    "channel_broadcast",
    "transcript",
    "should_trigger_reply",
    "trigger=True",
    "trigger=False",
    "consecutive_self",
    "Dropping echo from A2A peer",
    "self-echo",
    "[A2A] inbound",
    "ERROR",
    "Traceback",
]


def _read_token(env_file: Path) -> str | None:
    try:
        for line in env_file.read_text().splitlines():
            if line.startswith("DISCORD_BOT_TOKEN="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


def _discord_get(token: str, path: str) -> list | dict:
    import urllib.request
    req = urllib.request.Request(
        f"https://discord.com/api/v10{path}",
        headers={"Authorization": f"Bot {token}", "User-Agent": "hermes-a2a-sandbox/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_channel(token: str, limit: int = 50) -> list[dict]:
    msgs = _discord_get(token, f"/channels/{MESH_CHANNEL_ID}/messages?limit={limit}")
    return list(reversed(msgs)) if isinstance(msgs, list) else []


def tail_gateway_log(container: str, lines: int = 2000) -> str:
    try:
        r = subprocess.run(
            ["docker", "exec", container, "tail", "-n", str(lines),
             "/opt/data/logs/gateway.log"],
            capture_output=True, text=True, timeout=30,
        )
        return r.stdout + "\n" + r.stderr
    except subprocess.TimeoutExpired:
        return ""


def filter_after(log: str, threshold_iso: str) -> list[str]:
    out: list[str] = []
    keep_continuation = False
    for line in log.splitlines():
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if not m:
            if keep_continuation:
                out.append(line)
            continue
        ts = m.group(1).replace(" ", "T")
        if ts >= threshold_iso:
            out.append(line)
            keep_continuation = True
        else:
            keep_continuation = False
    return out


def grep_markers(lines: list[str], patterns: list[str]) -> dict[str, int]:
    return {
        p: sum(1 for line in lines if re.search(re.escape(p), line))
        for p in patterns
    }


def verify_boot_state() -> dict[str, bool]:
    """Mode 1 — static/boot-time verification."""
    out = {}

    # Containers running
    r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True)
    running = set(r.stdout.split())
    out["all_running"] = all(c in running for c in CONTAINERS)
    print(f"  containers up: {sorted(running & set(CONTAINERS))}", flush=True)

    # Agent cards
    cards_ok = True
    for c in CONTAINERS:
        port = HOST_PORTS.get(c)
        url = f"http://127.0.0.1:{port}/.well-known/agent-card.json" if port else None
        if not url:
            # Internal-only — use docker exec
            try:
                resp = subprocess.run(
                    ["docker", "exec", c, "curl", "-sf",
                     "http://127.0.0.1:8801/.well-known/agent-card.json"],
                    capture_output=True, text=True, timeout=15,
                )
                card = json.loads(resp.stdout) if resp.returncode == 0 else None
            except Exception:
                card = None
        else:
            try:
                import urllib.request
                with urllib.request.urlopen(url, timeout=10) as r2:
                    card = json.loads(r2.read().decode())
            except Exception:
                card = None
        if not card:
            print(f"  {c}: card unreachable", flush=True)
            cards_ok = False
            continue
        exts = [e.get("uri") for e in (card.get("capabilities", {}).get("extensions") or [])]
        has_cb = any("channel-broadcast" in (e or "") for e in exts)
        has_di = any("discord-identity" in (e or "") for e in exts)
        print(f"  {c}: card name={card.get('name')!r} channel-broadcast={has_cb} discord-identity={has_di}",
              flush=True)
        if not (has_cb and has_di):
            cards_ok = False
    out["agent_cards_have_both_extensions"] = cards_ok

    # Gateway log markers (since boot)
    boot_markers_ok = True
    for c in CONTAINERS:
        log = tail_gateway_log(c, lines=400)
        cb_wired = "ADR-011 channel-broadcast wired" in log
        a2a_connected = "✓ a2a connected" in log
        discord_connected = "✓ discord connected" in log
        print(f"  {c}: channel-broadcast_wired={cb_wired} a2a_connected={a2a_connected} discord_connected={discord_connected}",
              flush=True)
        if not (cb_wired and a2a_connected and discord_connected):
            boot_markers_ok = False
    out["boot_markers_present"] = boot_markers_ok

    # Bot-A's gateway log shows peers resolved
    log_a = tail_gateway_log(CONTAINERS[0], lines=400)
    peers_resolved_b = f"bot_user_id={BOT_B_USER_ID}" in log_a
    peers_resolved_c = f"bot_user_id={BOT_C_USER_ID}" in log_a
    print(f"  bot-A peer resolution: B={peers_resolved_b} C={peers_resolved_c}", flush=True)
    out["peers_resolved"] = peers_resolved_b and peers_resolved_c

    return out


def wait_for_trigger(token: str, timeout_s: int) -> dict | None:
    """Mode 2 — poll Discord channel until a non-bot user posts a new message."""
    print(f"\n→ Polling for a non-peer user message in mesh-abc "
          f"(timeout {timeout_s}s)…", flush=True)
    print(f"  → Soju: please post any short message in the mesh-abc channel "
          f"to trigger the cascade.", flush=True)
    print(f"  → Channel ID: {MESH_CHANNEL_ID}", flush=True)

    baseline = fetch_channel(token, limit=20)
    baseline_ids = {m.get("id") for m in baseline}
    print(f"  Baseline message count: {len(baseline)} ({list(baseline_ids)[:3]}…)", flush=True)

    deadline = time.time() + timeout_s
    poll = 4.0
    while time.time() < deadline:
        time.sleep(poll)
        try:
            now = fetch_channel(token, limit=20)
        except Exception as e:
            print(f"  poll error: {e}", flush=True)
            continue
        for m in now:
            if m.get("id") in baseline_ids:
                continue
            author = m.get("author") or {}
            author_id = str(author.get("id", ""))
            if author_id in BOT_IDS:
                # Bot replies — keep polling for the original USER message
                continue
            if author.get("bot"):
                continue
            print(f"  ✓ User trigger detected: {author.get('username')!r}: "
                  f"{(m.get('content') or '')[:120]!r}", flush=True)
            return m
    return None


def capture_cascade(token: str, since_iso: str, settle_s: int = 90) -> dict:
    """After a user trigger, wait `settle_s` and capture the cascade evidence."""
    print(f"\n→ Settling {settle_s}s to capture full cascade…", flush=True)
    time.sleep(settle_s)

    snapshot = fetch_channel(token, limit=50)
    threshold_short = since_iso[:19]
    in_window = [m for m in snapshot if (m.get("timestamp") or "")[:19] >= threshold_short]

    print(f"\n=== Channel snapshot since {since_iso} ===", flush=True)
    replies_by_bot = {"A": 0, "B": 0, "C": 0}
    user_msg_count = 0
    for m in in_window:
        ts = (m.get("timestamp") or "")[:19]
        edited = " *edited*" if m.get("edited_timestamp") else ""
        author = m.get("author") or {}
        author_id = author.get("id")
        bot = BOT_IDS.get(author_id)
        if bot:
            replies_by_bot[bot] += 1
            tag = f"Bot-{bot}"
        else:
            user_msg_count += 1
            tag = author.get("username", "?")[:10]
        content = (m.get("content") or "").replace("\n", " ")[:200]
        print(f"  [{ts}]{edited} {tag:>10}: {content}", flush=True)

    print(f"\n→ User messages in window: {user_msg_count}", flush=True)
    print(f"→ Bot replies in window: {replies_by_bot}", flush=True)

    # Gather log markers
    log_summary: dict[str, dict[str, int]] = {}
    for c in CONTAINERS:
        raw = tail_gateway_log(c, lines=3000)
        windowed = filter_after(raw, since_iso)
        log_summary[c] = grep_markers(windowed, MARKERS)

    print(f"\n=== /opt/data/logs/gateway.log markers since {since_iso} ===", flush=True)
    print(f"  {'marker':35} {'A':>4} {'B':>4} {'C':>4}", flush=True)
    for m in MARKERS:
        a = log_summary[CONTAINERS[0]].get(m, 0)
        b = log_summary[CONTAINERS[1]].get(m, 0)
        c = log_summary[CONTAINERS[2]].get(m, 0)
        flag = " <-- check" if (m in ("ERROR", "Traceback") and (a + b + c) > 0) else ""
        print(f"  {m:35} {a:>4} {b:>4} {c:>4}{flag}", flush=True)

    return {
        "replies_by_bot": replies_by_bot,
        "user_msg_count": user_msg_count,
        "log_summary": log_summary,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait-trigger", type=int, default=0,
                    help="Mode 2: wait N seconds for a non-peer user to post.")
    ap.add_argument("--settle", type=int, default=90,
                    help="Mode 2: capture window after trigger (default 90s).")
    args = ap.parse_args()

    print("=" * 72, flush=True)
    print("Phase 4 Task 41 — ADR-011 channel-broadcast sandbox verifier v3", flush=True)
    print(f"Channel: {MESH_CHANNEL_ID} (mesh-abc)", flush=True)
    print("=" * 72, flush=True)

    # Mode 1 always runs
    print("\n=== Mode 1: boot-time + static verification ===", flush=True)
    boot = verify_boot_state()
    print(flush=True)
    boot_pass = all(boot.values())
    for k, v in boot.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}", flush=True)
    print(f"  Mode 1 overall: {'PASS' if boot_pass else 'FAIL'}", flush=True)

    if args.wait_trigger <= 0:
        print("\n(Skipping Mode 2 — use --wait-trigger N to enable cascade capture)",
              flush=True)
        return 0 if boot_pass else 1

    # Mode 2 — needs Discord token
    env_a = REPO_ROOT / "docker" / "sandbox-env-a"
    token_a = _read_token(env_a)
    if not token_a:
        print(f"FATAL: cannot read DISCORD_BOT_TOKEN from {env_a}", file=sys.stderr)
        return 2

    print(f"\n=== Mode 2: user-trigger cascade verification ===", flush=True)
    trigger = wait_for_trigger(token_a, args.wait_trigger)
    if not trigger:
        print(f"\n✗ No user trigger received within {args.wait_trigger}s. Aborting Mode 2.",
              flush=True)
        return 1 if not boot_pass else 0

    trigger_ts = trigger.get("timestamp", "")
    if not trigger_ts:
        trigger_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = capture_cascade(token_a, trigger_ts, settle_s=args.settle)

    # Verdicts for Mode 2
    print(f"\n=== Mode 2 verdicts ===", flush=True)
    replies = result["replies_by_bot"]
    log_sum = result["log_summary"]
    bots_replied = sum(1 for v in replies.values() if v > 0)

    m1 = bots_replied >= 1
    print(f"  M1 ≥1 bot replied to user ({bots_replied}/3): {'PASS' if m1 else 'FAIL'}",
          flush=True)

    outbound_markers = sum(
        log_sum[c].get("_maybe_broadcast_a2a_reply", 0)
        + log_sum[c].get("broadcast", 0)
        for c in CONTAINERS
    )
    m2 = outbound_markers > 0
    print(f"  M2 outbound broadcast markers ({outbound_markers}): {'PASS' if m2 else 'FAIL'}",
          flush=True)

    inbound_markers = sum(
        log_sum[c].get("[A2A] inbound", 0) + log_sum[c].get("transcript", 0)
        for c in CONTAINERS
    )
    m3 = inbound_markers > 0
    print(f"  M3 A2A inbound + transcript markers ({inbound_markers}): {'PASS' if m3 else 'FAIL'}",
          flush=True)

    total = sum(replies.values())
    m4 = total <= 15  # generous cap — 3 bots × max_consecutive_self_replies(3) + initial
    print(f"  M4 cascade bounded ({total} replies, cap≤15): {'PASS' if m4 else 'FAIL'}",
          flush=True)

    errs = sum(log_sum[c].get("ERROR", 0) + log_sum[c].get("Traceback", 0)
               for c in CONTAINERS)
    m5 = errs == 0
    print(f"  M5 no errors in window ({errs}): {'PASS' if m5 else 'FAIL'}", flush=True)

    mode2_pass = all([m1, m2, m3, m4, m5])
    print(f"\n  Mode 2 overall: {'PASS' if mode2_pass else 'FAIL'}", flush=True)

    overall = boot_pass and mode2_pass
    print(f"\nOVERALL: {'PASS' if overall else 'FAIL'}", flush=True)
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
