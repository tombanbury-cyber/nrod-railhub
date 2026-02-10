#!/usr/bin/env python3
"""
Curses-based interactive dashboard for nrod_railhub.

Provides a real-time terminal UI showing train movements from VSTP, TRUST, and TD feeds.
Adapted from experimental/td_feed_dashboard.py to work with the main nrod_railhub architecture.
"""

import curses
import logging
import queue
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Optional, Deque, List, Tuple

from .views import HumanView
from .listener import Listener


# Color pair constants
CP_OK = 1
CP_WARN = 2
CP_ERR = 3
CP_TITLE = 4
CP_DIM = 5
CP_BORDER = 6


class QueueHandler(logging.Handler):
    """Logging handler that sends log messages to a queue."""
    
    def __init__(self, log_queue: "queue.Queue[str]"):
        super().__init__()
        self.log_queue = log_queue
    
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if not self.log_queue.full():
                self.log_queue.put(msg)
        except Exception:
            self.handleError(record)


@dataclass
class InteractiveDashboardState:
    """State for the interactive curses dashboard."""
    
    headcode_filter: Optional[str] = None
    uid_filter: Optional[str] = None
    td_area_filter: Optional[List[str]] = None
    
    connected: bool = False
    connection_time: Optional[str] = None
    last_message_time: Optional[str] = None
    
    total_messages: int = 0
    msg_count_by_dest: dict = field(default_factory=dict)
    
    # Ring buffer for recent console output (TD messages)
    console_lines: Deque[str] = field(default_factory=lambda: deque(maxlen=500))
    
    # Ring buffer for TRUST messages
    trust_lines: Deque[str] = field(default_factory=lambda: deque(maxlen=500))
    
    # Ring buffer for VSTP messages
    vstp_lines: Deque[str] = field(default_factory=lambda: deque(maxlen=500))
    
    # Ring buffer for error log messages
    error_lines: Deque[str] = field(default_factory=lambda: deque(maxlen=500))
    
    # Ring buffer for database insert messages
    db_lines: Deque[str] = field(default_factory=lambda: deque(maxlen=500))
    
    # Ring buffer for HTTP request messages
    http_lines: Deque[str] = field(default_factory=lambda: deque(maxlen=500))
    
    # Message rate tracking
    _rx_times: Deque[float] = field(default_factory=lambda: deque(maxlen=200))
    
    # Page navigation
    current_page: int = 0  # 0=TD, 1=TRUST, 2=VSTP, 3=Error, 4=DB, 5=HTTP
    
    paused: bool = False
    
    # Last redraw time for periodic refresh
    last_redraw: float = field(default_factory=time.time)
    
    def note_message(self, destination: str) -> None:
        """Record a message receipt."""
        self.total_messages += 1
        self.msg_count_by_dest[destination] = self.msg_count_by_dest.get(destination, 0) + 1
        self._rx_times.append(time.time())
    
    def add_console_line(self, line: str) -> None:
        """Add a line to the console output."""
        self.console_lines.append(line)
    
    def add_trust_line(self, line: str) -> None:
        """Add a line to the TRUST messages output."""
        self.trust_lines.append(line)
    
    def add_vstp_line(self, line: str) -> None:
        """Add a line to the VSTP messages output."""
        self.vstp_lines.append(line)
    
    def add_error_line(self, line: str) -> None:
        """Add a line to the error log."""
        self.error_lines.append(line)
    
    def add_db_line(self, line: str) -> None:
        """Add a line to the database inserts log."""
        self.db_lines.append(line)
    
    def add_http_line(self, line: str) -> None:
        """Add a line to the HTTP requests log."""
        self.http_lines.append(line)
    
    def rate_messages_per_min(self) -> float:
        """Calculate message rate per minute."""
        if len(self._rx_times) < 2:
            return 0.0
        dt = self._rx_times[-1] - self._rx_times[0]
        if dt <= 0:
            return 0.0
        return (len(self._rx_times) / dt) * 60.0


def _init_colors() -> None:
    """Initialize curses color pairs."""
    try:
        curses.start_color()
        if hasattr(curses, "use_default_colors"):
            curses.use_default_colors()
        curses.init_pair(CP_OK, curses.COLOR_GREEN, -1)
        curses.init_pair(CP_WARN, curses.COLOR_YELLOW, -1)
        curses.init_pair(CP_ERR, curses.COLOR_RED, -1)
        curses.init_pair(CP_TITLE, curses.COLOR_CYAN, -1)
        curses.init_pair(CP_DIM, curses.COLOR_BLUE, -1)
        curses.init_pair(CP_BORDER, curses.COLOR_WHITE, -1)
    except Exception:
        pass


def _cattr(pair_id: int, extra: int = 0) -> int:
    """Get color attribute for a color pair."""
    try:
        return curses.color_pair(pair_id) | extra
    except Exception:
        return extra


def _safe_addstr(win, y: int, x: int, s: str, attr: int = 0) -> None:
    """Safely add a string to a window, truncating if necessary."""
    try:
        max_x = win.getmaxyx()[1]
        if x < max_x:
            win.addnstr(y, x, s, max(0, max_x - x - 1), attr)
    except curses.error:
        pass


def _draw_box_title(win, title: str, title_attr: int = 0) -> None:
    """Draw a box with a title."""
    try:
        win.attron(_cattr(CP_BORDER, curses.A_DIM))
        win.border()
        win.attroff(_cattr(CP_BORDER, curses.A_DIM))
        _safe_addstr(win, 0, 2, f" {title} ", title_attr or _cattr(CP_TITLE, curses.A_BOLD))
    except curses.error:
        pass


def _render_header(stdscr, state: InteractiveDashboardState, header_h: int, w: int) -> None:
    """Render the header section showing connection status and stats."""
    header = stdscr.derwin(header_h, w, 0, 0)
    _draw_box_title(header, " nrod-railhub Interactive Dashboard ", _cattr(CP_TITLE, curses.A_BOLD))
    
    # Connection status
    conn_icon = "●"
    conn_str = "CONNECTED" if state.connected else "DISCONNECTED"
    conn_attr = _cattr(CP_OK, curses.A_BOLD) if state.connected else _cattr(CP_ERR, curses.A_BOLD)
    _safe_addstr(header, 1, 2, f"{conn_icon} {conn_str}", conn_attr)
    
    # Filters
    filters = []
    if state.headcode_filter:
        filters.append(f"headcode={state.headcode_filter}")
    if state.uid_filter:
        filters.append(f"uid={state.uid_filter}")
    if state.td_area_filter:
        filters.append(f"areas={','.join(state.td_area_filter)}")
    
    filter_str = "  ".join(filters) if filters else "No filters"
    _safe_addstr(header, 1, 20, filter_str, _cattr(CP_DIM))
    
    # Message stats
    rate = f"{state.rate_messages_per_min():.1f} msg/min"
    _safe_addstr(header, 2, 2, f"Messages: {state.total_messages}  ({rate})", _cattr(CP_DIM))
    
    # Last message time
    if state.last_message_time:
        _safe_addstr(header, 3, 2, f"Last: {state.last_message_time}", _cattr(CP_DIM))
    
    if state.paused:
        _safe_addstr(header, 1, w - 14, "⏸ PAUSED", _cattr(CP_WARN, curses.A_BOLD))
    
    header.noutrefresh()


def _render_console(stdscr, state: InteractiveDashboardState, y0: int, body_h: int, w: int) -> None:
    """Render the console output section based on current page."""
    console = stdscr.derwin(body_h, w, y0, 0)
    
    # Page titles and content
    page_names = [
        "TD Messages",
        "TRUST Messages",
        "VSTP Messages",
        "Error Log",
        "Database Inserts",
        "HTTP Requests"
    ]
    page_lines = [
        state.console_lines,
        state.trust_lines,
        state.vstp_lines,
        state.error_lines,
        state.db_lines,
        state.http_lines
    ]
    
    current_page_name = page_names[state.current_page]
    current_lines = page_lines[state.current_page]
    
    _draw_box_title(console, f" {current_page_name} (Page {state.current_page + 1}/6) ", _cattr(CP_TITLE, curses.A_BOLD))
    
    # Show recent lines
    max_lines = max(0, body_h - 2)
    lines = list(current_lines)[-max_lines:]
    
    for i, line in enumerate(lines):
        y = i + 1
        if y >= body_h - 1:
            break
        _safe_addstr(console, y, 2, line, 0)
    
    console.noutrefresh()


def _render_footer(stdscr, h: int, w: int) -> None:
    """Render the footer with key bindings."""
    footer_y = h - 1
    help_text = "q=quit  p=pause  c=clear  Tab/1-6=pages"
    try:
        stdscr.addnstr(footer_y, 2, help_text, w - 4, _cattr(CP_DIM))
    except curses.error:
        pass


def dashboard_loop(stdscr, state: InteractiveDashboardState, listener: Listener, queues: dict, stop_event: threading.Event) -> None:
    """Main dashboard rendering loop."""
    stdscr.nodelay(True)
    stdscr.timeout(50)
    stdscr.keypad(True)
    _init_colors()
    
    # Periodic redraw interval (seconds) to prevent screen corruption
    REDRAW_INTERVAL = 60.0
    
    while not stop_event.is_set():
        try:
            ch = stdscr.getch()
        except Exception:
            ch = -1
        
        # Handle key input
        if ch != -1:
            if ch in (ord("q"), ord("Q")):
                stop_event.set()
                break
            elif ch in (ord("p"), ord("P")):
                state.paused = not state.paused
            elif ch in (ord("c"), ord("C")):
                # Clear current page's buffer
                page_buffers = [
                    state.console_lines,
                    state.trust_lines,
                    state.vstp_lines,
                    state.error_lines,
                    state.db_lines,
                    state.http_lines
                ]
                if 0 <= state.current_page < len(page_buffers):
                    page_buffers[state.current_page].clear()
            elif ch == ord("\t") or ch == 9:  # Tab key
                state.current_page = (state.current_page + 1) % 6
            elif ch in (ord("1"), ord("2"), ord("3"), ord("4"), ord("5"), ord("6")):
                # Number keys 1-6 for direct page access
                state.current_page = ch - ord("1")
        
        # Update state from listener
        if not state.paused:
            state.connected = listener.connected_at is not None
            state.connection_time = listener.connected_at
            state.last_message_time = listener.last_message_at
            state.total_messages = listener.msg_count_total
            state.msg_count_by_dest = dict(listener.msg_count_by_dest)
            
            # Pull output from all queues
            queue_handlers = [
                (queues.get('td'), state.add_console_line),
                (queues.get('trust'), state.add_trust_line),
                (queues.get('vstp'), state.add_vstp_line),
                (queues.get('error'), state.add_error_line),
                (queues.get('db'), state.add_db_line),
                (queues.get('http'), state.add_http_line),
            ]
            
            for queue_obj, add_method in queue_handlers:
                if queue_obj:
                    try:
                        while True:
                            line = queue_obj.get_nowait()
                            if line:
                                add_method(line)
                                if queue_obj == queues.get('td'):
                                    state.note_message("output")  # Track for rate calculation
                    except queue.Empty:
                        pass
        
        # Check if periodic redraw is needed
        current_time = time.time()
        if current_time - state.last_redraw >= REDRAW_INTERVAL:
            stdscr.clear()  # Force full redraw
            state.last_redraw = current_time
        
        # Render UI
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        
        header_h = 5
        footer_h = 1
        body_h = max(0, h - header_h - footer_h)
        
        _render_header(stdscr, state, header_h, w)
        _render_console(stdscr, state, header_h, body_h, w)
        _render_footer(stdscr, h, w)
        
        stdscr.noutrefresh()
        curses.doupdate()
        time.sleep(0.05)


def run_interactive_dashboard(
    listener: Listener,
    output_queue: "queue.Queue[str]",
    trust_queue: Optional["queue.Queue[str]"] = None,
    vstp_queue: Optional["queue.Queue[str]"] = None,
    error_queue: Optional["queue.Queue[str]"] = None,
    db_queue: Optional["queue.Queue[str]"] = None,
    http_queue: Optional["queue.Queue[str]"] = None,
    headcode: Optional[str] = None,
    uid: Optional[str] = None,
    td_area: Optional[List[str]] = None,
) -> None:
    """
    Run the interactive curses dashboard.
    
    Args:
        listener: The STOMP listener instance
        output_queue: Queue containing console output lines from the listener (TD messages)
        trust_queue: Optional queue for TRUST messages
        vstp_queue: Optional queue for VSTP messages
        error_queue: Optional queue for error log messages
        db_queue: Optional queue for database insert messages
        http_queue: Optional queue for HTTP request messages
        headcode: Optional headcode filter
        uid: Optional UID filter
        td_area: Optional list of TD area filters
    """
    state = InteractiveDashboardState(
        headcode_filter=headcode,
        uid_filter=uid,
        td_area_filter=td_area,
    )
    
    stop_event = threading.Event()
    
    # Store queues for access in dashboard loop
    queues = {
        'td': output_queue,
        'trust': trust_queue,
        'vstp': vstp_queue,
        'error': error_queue,
        'db': db_queue,
        'http': http_queue,
    }
    
    try:
        curses.wrapper(dashboard_loop, state=state, listener=listener, queues=queues, stop_event=stop_event)
    finally:
        stop_event.set()
