"""Task 9 — A2A peer registry resolver (Tier-1, static config)."""

from __future__ import annotations

from gateway.platforms.a2a_registry import resolve_a2a_peer_url


def test_known_peer():
    cfg = {"peers": {"1234567890": "http://10.0.0.1:8765/"}}
    assert resolve_a2a_peer_url("1234567890", cfg) == "http://10.0.0.1:8765/"


def test_unknown_peer():
    assert resolve_a2a_peer_url("9999", {"peers": {}}) is None


def test_no_peers_key():
    """Config exists but no `peers` block — treated as empty registry."""
    assert resolve_a2a_peer_url("1234567890", {"enabled": True}) is None


def test_no_config():
    """A2A disabled → resolver short-circuits to None (no AttributeError)."""
    assert resolve_a2a_peer_url("1", None) is None


def test_empty_config():
    assert resolve_a2a_peer_url("1", {}) is None


def test_int_id_normalized():
    """Discord hands int IDs from the wire; resolver coerces to str."""
    cfg = {"peers": {"1234567890": "http://x/"}}
    assert resolve_a2a_peer_url(1234567890, cfg) == "http://x/"


def test_peers_none_value():
    """`peers: null` in YAML deserializes to None — `or {}` handles it."""
    assert resolve_a2a_peer_url("1", {"peers": None}) is None
