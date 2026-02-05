#!/usr/bin/env python3
"""Tests for curses_view module."""

import pytest
import queue
from unittest.mock import Mock, MagicMock
from nrod_railhub.curses_view import (
    InteractiveDashboardState,
    _init_colors,
    _cattr,
)


def test_interactive_dashboard_state_creation():
    """Test creating InteractiveDashboardState."""
    state = InteractiveDashboardState(
        headcode_filter="2C90",
        uid_filter="C43876",
        td_area_filter=["EK", "AD"],
    )
    
    assert state.headcode_filter == "2C90"
    assert state.uid_filter == "C43876"
    assert state.td_area_filter == ["EK", "AD"]
    assert state.connected is False
    assert state.total_messages == 0
    assert len(state.console_lines) == 0


def test_dashboard_state_note_message():
    """Test message tracking in dashboard state."""
    state = InteractiveDashboardState()
    
    state.note_message("/topic/VSTP_ALL")
    state.note_message("/topic/TRUST_ALL")
    state.note_message("/topic/VSTP_ALL")
    
    assert state.total_messages == 3
    assert state.msg_count_by_dest["/topic/VSTP_ALL"] == 2
    assert state.msg_count_by_dest["/topic/TRUST_ALL"] == 1


def test_dashboard_state_add_console_line():
    """Test adding console lines to dashboard state."""
    state = InteractiveDashboardState()
    
    state.add_console_line("Line 1")
    state.add_console_line("Line 2")
    state.add_console_line("Line 3")
    
    assert len(state.console_lines) == 3
    assert list(state.console_lines) == ["Line 1", "Line 2", "Line 3"]


def test_dashboard_state_console_lines_maxlen():
    """Test that console lines respect maxlen."""
    state = InteractiveDashboardState()
    
    # Add more lines than maxlen (500)
    for i in range(600):
        state.add_console_line(f"Line {i}")
    
    assert len(state.console_lines) == 500
    # Should have kept the most recent 500
    assert list(state.console_lines)[0] == "Line 100"
    assert list(state.console_lines)[-1] == "Line 599"


def test_dashboard_state_rate_messages_per_min():
    """Test message rate calculation."""
    import time
    
    state = InteractiveDashboardState()
    
    # Rate should be 0 with no messages
    assert state.rate_messages_per_min() == 0.0
    
    # Rate should be 0 with only one message
    state.note_message("/topic/TEST")
    assert state.rate_messages_per_min() == 0.0
    
    # Add multiple messages with simulated time
    base_time = time.time()
    state._rx_times.clear()
    
    # Simulate 10 messages over 1 second = 600 messages/min
    for i in range(10):
        state._rx_times.append(base_time + i * 0.1)
    
    rate = state.rate_messages_per_min()
    # Should be approximately 600 msg/min (10 messages / 0.9 seconds * 60)
    assert 600 <= rate <= 700


def test_cattr_function():
    """Test color attribute function (basic functionality)."""
    # Should not crash even if curses isn't initialized
    result = _cattr(1, 0)
    assert isinstance(result, int)


def test_init_colors_no_crash():
    """Test that _init_colors doesn't crash even without curses."""
    # Should handle the case where curses isn't available or initialized
    try:
        _init_colors()
    except Exception as e:
        # If it does raise an exception, it should be handled
        pytest.fail(f"_init_colors raised an exception: {e}")
