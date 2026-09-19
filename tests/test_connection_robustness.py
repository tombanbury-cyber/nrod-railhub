#!/usr/bin/env python3
"""Tests for connection robustness improvements."""

import argparse
import threading
import time
from unittest.mock import Mock

from nrod_railhub.listener import Listener
from nrod_railhub.views import HumanView


def _make_args(**overrides):
    defaults = {
        "verbose": False,
        "trace_headcode": False,
        "headcode": None,
        "uid": None,
        "td_area": None,
        "width": 96,
        "only_changes": True,
        "repeat_after": 300,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_on_connected_triggers_subscribe_callback():
    """Re-subscription callback must be invoked on every CONNECTED frame."""
    hv = Mock(spec=HumanView)
    args = _make_args()
    called = threading.Event()

    def subscribe_cb():
        called.set()

    listener = Listener(hv, args, subscribe_callback=subscribe_cb)
    frame = Mock()
    frame.headers = {"session": "s1", "server": "artemis", "version": "1.1"}

    listener.on_connected(frame)

    assert called.is_set()
    assert listener.connected_at is not None
    assert listener._reconnect_count == 1


def test_on_connected_counts_reconnects():
    """Each CONNECTED frame increments the reconnect counter."""
    hv = Mock(spec=HumanView)
    args = _make_args()
    counter = {"n": 0}

    def subscribe_cb():
        counter["n"] += 1

    listener = Listener(hv, args, subscribe_callback=subscribe_cb)
    frame = Mock()
    frame.headers = {}

    listener.on_connected(frame)
    listener.on_connected(frame)

    assert listener._reconnect_count == 2
    assert counter["n"] == 2


def test_on_connected_subscribe_error_is_caught():
    """Listener must not crash if the subscribe callback raises."""
    hv = Mock(spec=HumanView)
    args = _make_args()

    def bad_subscribe():
        raise RuntimeError("boom")

    listener = Listener(hv, args, subscribe_callback=bad_subscribe)
    frame = Mock()
    frame.headers = {}

    listener.on_connected(frame)

    assert listener.connected_at is not None


def test_lifecycle_hooks_do_not_crash():
    """on_disconnected, on_heartbeat_timeout and on_receiver_loop_completed must be safe."""
    hv = Mock(spec=HumanView)
    args = _make_args()
    listener = Listener(hv, args)
    frame = Mock()
    frame.headers = {}

    listener.on_disconnected()
    listener.on_heartbeat_timeout()
    listener.on_receiver_loop_completed(frame)


def test_connection_watchdog_triggers_on_silence(monkeypatch):
    """Watchdog must disconnect when no message has arrived for max_silence seconds."""
    from nrod_railhub.cli import start_connection_watchdog

    conn = Mock()
    conn.is_connected.return_value = True

    listener = Mock()

    disconnect_event = threading.Event()

    def disconnect():
        disconnect_event.set()

    conn.disconnect = disconnect

    # Pick timestamps that are max_silence+1 seconds apart.
    listener.last_message_at = "2026-09-19T12:00:00+00:00"
    future_ts = 1789819200.0 + 61.0

    monkeypatch.setattr(time, "time", lambda: future_ts)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    t = start_connection_watchdog(conn, listener, max_silence=60, check_interval=1)
    t.join(timeout=0.5)

    assert disconnect_event.is_set()
