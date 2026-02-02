#!/usr/bin/env python3
"""
Flask-based lightweight web dashboard for nrod-railhub.

This file refactors route rendering to use a shared header/footer and adds
a persistent top navigation bar with a quick filter form. Styles are kept
simple and inline to avoid adding new dependencies.
"""

from __future__ import annotations

import pathlib
import sqlite3
from typing import List

from flask import Flask, request

def start_web_dashboard(db_path: str, port: int) -> None:
    app = Flask(__name__)
    db_path = str(pathlib.Path(db_path).expanduser())
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000;")

    def q(sql: str, params=()):
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows

    # Shared UI parts
    def nav_html(active: str = "") -> str:
        area = request.args.get("area", "").strip()
        hc = request.args.get("hc", "").strip()
        # quick filter form posts as GET to home
        return (
            "<div class='topnav'>"
            "<div class='brand'><a href='/' class='brand-link'>NR RailHub</a></div>"
            "<div class='links'>"
            f"<a href='/' class='navlink {'active' if active=='home' else ''}'>Home</a>"
            f"<a href='/events' class='navlink {'active' if active=='events' else ''}'>Events</a>"
            f"<a href='/raw-events' class='navlink {'active' if active=='raw' else ''}'>Raw Events</a>"
            f"<a href='/signals' class='navlink {'active' if active=='signals' else ''}'>Signals</a>"
            f"<a href='/stats' class='navlink {'active' if active=='stats' else ''}'>Stats</a>"
            "</div>"
            "<div class='quickfilter'>"
            f"<form method='get' action='/'><input name='area' placeholder='Area' value='{area}' size='4'/> "
            f"<input name='hc' placeholder='Headcode' value='{hc}' size='6'/> "
            "<button type='submit'>Filter</button></form>"
            "</div>"
            "</div>"
        )

    def render_page(title: str, body_parts: List[str], active: str = "home", auto_refresh: int = 0) -> str:
        # body_parts is a list of HTML fragments; they will be joined
        refresh_meta = f"<meta http-equiv='refresh' content='{auto_refresh}'>" if auto_refresh and auto_refresh > 0 else ""
        head = (
            "<html><head><meta charset='utf-8'>"
            f"<title>{title}</title>"
            f"{refresh_meta}"
            "<style>"
            "body{font-family:system-ui,Arial;margin:0;padding:0;background:#fff;color:#222}"
            ".container{max-width:1100px;margin:90px auto 40px;padding:20px}"
            ".topnav{position:fixed;top:0;left:0;right:0;height:64px;background:#0b5cff;color:white;display:flex;align-items:center;padding:0 14px;box-shadow:0 2px 6px rgba(0,0,0,0.08);z-index:999}"
            ".brand{font-weight:700;margin-right:18px}"
            ".brand-link{color:white;text-decoration:none;font-size:18px}"
            ".links{display:flex;gap:12px;margin-right:20px}"
            ".navlink{color:rgba(255,255,255,0.9);text-decoration:none;padding:8px 10px;border-radius:6px;font-size:14px}"
            ".navlink:hover{background:rgba(255,255,255,0.06)}"
            ".navlink.active{background:rgba(0,0,0,0.12)}"
            ".quickfilter{margin-left:auto}"
            ".quickfilter input{padding:6px 8px;border-radius:6px;border:0;margin-right:6px}"
            ".quickfilter button{padding:6px 8px;border-radius:6px;border:0;background:#ffd24d;color:#222}"
            "table{border-collapse:collapse;width:100%;background:white;margin-top:8px}"
            "th,td{border-bottom:1px solid #eee;padding:8px 10px;font-size:14px;text-align:left}"
            "th{background:#f7f9fc;font-weight:600}"
            ".pill{display:inline-block;padding:4px 8px;border-radius:999px;background:#eef3ff;margin-right:6px;font-size:13px}"
            ".dim{color:#6c757d;font-size:12px}"
            ".mono{font-family:monospace}"
            "</style>"
            "</head><body>"
        )
        body = "<div class='container'>" + nav_html(active) + "".join(body_parts) + "</div>"
        foot = "</body></html>"
        return "\n".join([head, body, foot])

    @app.get("/")
    def index():
        counts = q("SELECT (SELECT COUNT(*) FROM td_state) AS td_state, (SELECT COUNT(*) FROM td_event) AS td_event, (SELECT COUNT(*) FROM trust_state) AS trust_state, (SELECT COUNT(*) FROM vstp_state) AS vstp_state")[0]

        area = request.args.get("area", "").strip()
        hc_filter = request.args.get("hc", "").strip()
        if area:
            rows = q("SELECT * FROM td_state WHERE td_area=? ORDER BY last_time_utc DESC LIMIT 200", (area,))
        elif hc_filter:
            rows = q("SELECT * FROM td_state WHERE headcode=? ORDER BY last_time_utc DESC LIMIT 200", (hc_filter,))
        else:
            rows = q("SELECT * FROM td_state ORDER BY last_time_utc DESC LIMIT 200")
        areas = [r[0] for r in q("SELECT DISTINCT td_area FROM td_state ORDER BY td_area")]
        body = []
        body.append(f"<p><b>DB:</b> td_state={counts['td_state']} td_event={counts['td_event']} trust_state={counts['trust_state']} vstp_state={counts['vstp_state']}</p>")
        # area pills
        body.append("<div>Filter: " + " ".join([f"<a class='pill' href='/?area={a}'>{a}</a>" for a in areas]) + " <a class='pill' href='/'>ALL</a></div>")
        body.append("<h3>Latest TD state" + (f" (area {area})" if area else "") + "</h3>")
        body.append("<table><tr><th>Area</th><th>Headcode</th><th>Time</th><th>From</th><th>To</th><th>Location</th><th>Plat</th><th>Sched</th></tr>")
        for r in rows:
            # Build schedule string similar to original
            sched = ""
            if r.get("sched_dep") or r.get("sched_arr"):
                sched = f"{r.get('sched_dep') or ''}→{r.get('sched_arr') or ''} {r.get('origin_name') or ''}→{r.get('dest_name') or ''}"
            # Build location string similar to original
            loc = r.get("location_name") or ""
            if r.get("stanox"):
                loc = f"{loc} ({r['stanox']})".strip()
            body.append("<tr>" + "".join([
                f"<td>{r['td_area']}</td>",
                f"<td><a href='/train?area={r['td_area']}&hc={r['headcode']}'>{r['headcode']}</a></td>",
                f"<td class='mono dim'>{r.get('last_time_utc','')}</td>",
                f"<td>{r.get('from_berth','')}</td>",
                f"<td>{r.get('to_berth','')}</td>",
                f"<td>{loc}</td>",
                f"<td>{r.get('platform','')}</td>",
                f"<td>{sched}</td>",
            ]) + "</tr>")
        body.append("</table>")
        # auto-refresh if ?refresh=N provided
        refresh_sec = 0
        try:
            refresh_sec = int(request.args.get("refresh", "0") or 0)
        except Exception:
            refresh_sec = 0
        return render_page("Home - NR RailHub", body, active="home", auto_refresh=refresh_sec)

    @app.get("/train")
    def train():
        area = request.args.get("area", "")
        hc = request.args.get("hc", "")
        st = q("SELECT * FROM td_state WHERE td_area=? AND headcode=?", (area, hc))
        ev = q("SELECT * FROM td_event WHERE td_area=? AND headcode=? ORDER BY ts_utc DESC LIMIT 200", (area, hc))
        body = [f"<h2>{area} / {hc}</h2>"]
        body.append(f"<p><a href='/'>Back</a></p>")
        if st:
            r = st[0]
            body.append("<pre>" + str(dict(r)) + "</pre>")
        if ev:
            body.append("<h3>Recent events</h3><table><tr><th>Time</th><th>Type</th><th>From</th><th>To</th></tr>")
            for r in ev:
                body.append(f"<tr><td class='mono'>{r['ts_utc']}</td><td>{r['event_type']}</td><td>{r['from_berth']}</td><td>{r['to_berth']}</td></tr>")
            body.append("</table>")
        return render_page(f"Train {hc}", body, active="home")

    @app.get("/events")
    def events():
        rows = q("SELECT ts_utc, td_area, headcode, event_type, from_berth, to_berth FROM td_event ORDER BY ts_utc DESC LIMIT 500")
        body = ["<h2>Recent TD events</h2><p></p>"]
        body.append("<table><tr><th>Time</th><th>Area</th><th>Headcode</th><th>Type</th><th>From</th><th>To</th></tr>")
        for r in rows:
            body.append(f"<tr><td class='mono'>{r['ts_utc']}</td><td>{r['td_area']}</td><td>{r['headcode']}</td><td>{r['event_type']}</td><td>{r['from_berth']}</td><td>{r['to_berth']}</td></tr>")
        body.append("</table>")
        return render_page("Events - NR RailHub", body, active="events")

    @app.get("/signals")
    def signals():
        # Reuse existing query from previous implementation
        rows = q("SELECT * FROM trust_state ORDER BY td_area, headcode LIMIT 500")
        body = ["<h2>Signal Mapper</h2>"]
        body.append("<table><tr><th>Area</th><th>Headcode</th><th>Data</th></tr>")
        for r in rows:
            body.append(f"<tr><td>{r['td_area']}</td><td>{r['headcode']}</td><td class='mono dim'>{r.get('data','')}</td></tr>")
        body.append("</table>")
        return render_page("Signals - NR RailHub", body, active="signals")

    @app.get("/raw-events")
    def raw_events():
        msg_type = request.args.get("msg_type", "").strip()
        area = request.args.get("area", "").strip()

        body = ["<h2>Raw TD Events</h2>"]
        # Determine table name (td_events preferred)
        table_name = None
        try:
            table_check = q("SELECT name FROM sqlite_master WHERE type='table' AND name='td_events'")
            if table_check:
                table_name = "td_events"
            else:
                table_check = q("SELECT name FROM sqlite_master WHERE type='table' AND name='td_event'")
                if table_check:
                    table_name = "td_event"
        except Exception:
            pass

        if not table_name:
            body.append("<p><i>No TD events table found in DB</i></p>")
            return render_page("Raw Events - NR RailHub", body, active="raw")

        sql = f"SELECT * FROM {table_name} WHERE 1=1"
        params = []
        if msg_type:
            sql += " AND event_type=?"
            params.append(msg_type)
        if area:
            sql += " AND td_area=?"
            params.append(area)
        sql += " ORDER BY ts_utc DESC LIMIT 500"
        try:
            rows = q(sql, params)
            if rows:
                # table header from row keys
                keys = rows[0].keys()
                body.append("<table><tr>" + "".join(f"<th>{k}</th>" for k in keys) + "</tr>")
                for r in rows:
                    row_data = [str(r[k]) for k in keys]
                    # keep last two columns monospace for readability (as original code did)
                    body.append("<tr>" + "".join([f"<td class='{'mono' if i>=len(row_data)-2 else ''}'>{d}</td>" for i, d in enumerate(row_data)]) + "</tr>")
                body.append("</table>")
                body.append(f"<p class='dim'>Showing {len(rows)} event(s) from {table_name} table</p>")
            else:
                body.append("<p><i>No events matching filters</i></p>")
        except Exception as e:
            body.append(f"<p><i>Error querying events: {e}</i></p>")
        return render_page("Raw Events - NR RailHub", body, active="raw")

    @app.get("/stats")
    def stats():
        body = ["<h2>Stats</h2>"]
        try:
            counts = q("SELECT (SELECT COUNT(*) FROM td_state) AS td_state, (SELECT COUNT(*) FROM td_event) AS td_event")[0]
            body.append(f"<p class='dim'>td_state={counts['td_state']} td_event={counts['td_event']}</p>")
        except Exception as e:
            body.append(f"<p><i>Error fetching stats: {e}</i></p>")
        return render_page("Stats - NR RailHub", body, active="stats")

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

