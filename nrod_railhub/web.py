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
            f"<a href='/mapper' class='navlink {'active' if active=='mapper' else ''}'>Mapper</a>"
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
        # Count both berth and signal events
        counts = q("""
            SELECT 
                (SELECT COUNT(*) FROM td_state) AS td_state, 
                (SELECT COUNT(*) FROM td_berth_events) + (SELECT COUNT(*) FROM td_signal_events) AS td_event,
                (SELECT COUNT(*) FROM trust_state) AS trust_state, 
                (SELECT COUNT(*) FROM vstp_state) AS vstp_state
        """)[0]

        area = request.args.get("area", "").strip()
        hc_filter = request.args.get("hc", "").strip()
        if area:
            rows = q("SELECT * FROM td_state WHERE td_area=? ORDER BY last_time_ms DESC LIMIT 200", (area,))
        elif hc_filter:
            rows = q("SELECT * FROM td_state WHERE headcode=? ORDER BY last_time_ms DESC LIMIT 200", (hc_filter,))
        else:
            rows = q("SELECT * FROM td_state ORDER BY last_time_ms DESC LIMIT 200")
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
            sched_dep = r["sched_dep"] if "sched_dep" in r.keys() and r["sched_dep"] else None
            sched_arr = r["sched_arr"] if "sched_arr" in r.keys() and r["sched_arr"] else None
            if sched_dep or sched_arr:
                origin = r["origin_name"] if "origin_name" in r.keys() and r["origin_name"] else ""
                dest = r["dest_name"] if "dest_name" in r.keys() and r["dest_name"] else ""
                sched = f"{sched_dep or ''}→{sched_arr or ''} {origin}→{dest}"
            # Build location string similar to original
            loc = r["location_name"] if "location_name" in r.keys() and r["location_name"] else ""
            stanox = r["stanox"] if "stanox" in r.keys() and r["stanox"] else ""
            if stanox:
                loc = f"{loc} ({stanox})".strip()
            body.append("<tr>" + "".join([
                f"<td>{r['td_area']}</td>",
                f"<td><a href='/train?area={r['td_area']}&hc={r['headcode']}'>{r['headcode']}</a></td>",
                f"<td class='mono dim'>{r['last_time_iso'] if r['last_time_iso'] else ''}</td>",
                f"<td>{r['from_berth'] if r['from_berth'] else ''}</td>",
                f"<td>{r['to_berth'] if r['to_berth'] else ''}</td>",
                f"<td>{loc}</td>",
                f"<td>{r['platform'] if 'platform' in r.keys() and r['platform'] else ''}</td>",
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
        # Query both berth and signal events
        ev = q("SELECT ts_ms, ts_iso, msg_type, from_berth, to_berth, descr FROM td_berth_events WHERE td_area=? AND headcode=? ORDER BY ts_ms DESC LIMIT 200", (area, hc))
        body = [f"<h2>{area} / {hc}</h2>"]
        body.append(f"<p><a href='/'>Back</a></p>")
        if st:
            r = st[0]
            body.append("<pre>" + str(dict(r)) + "</pre>")
        if ev:
            body.append("<h3>Recent berth events</h3><table><tr><th>Time</th><th>Type</th><th>From</th><th>To</th></tr>")
            for r in ev:
                body.append(f"<tr><td class='mono'>{r['ts_iso']}</td><td>{r['msg_type']}</td><td>{r['from_berth']}</td><td>{r['to_berth']}</td></tr>")
            body.append("</table>")
        return render_page(f"Train {hc}", body, active="home")

    @app.get("/events")
    def events():
        # Query berth events
        rows = q("SELECT ts_ms, ts_iso, td_area, headcode, msg_type, from_berth, to_berth FROM td_berth_events ORDER BY ts_ms DESC LIMIT 500")
        body = ["<h2>Recent TD berth events</h2><p></p>"]
        body.append("<table><tr><th>Time</th><th>Area</th><th>Headcode</th><th>Type</th><th>From</th><th>To</th></tr>")
        for r in rows:
            body.append(f"<tr><td class='mono'>{r['ts_iso']}</td><td>{r['td_area']}</td><td>{r['headcode']}</td><td>{r['msg_type']}</td><td>{r['from_berth']}</td><td>{r['to_berth']}</td></tr>")
        body.append("</table>")
        return render_page("Events - NR RailHub", body, active="events")

    @app.get("/signals")
    def signals():
        # Query TD signal events
        rows = q("SELECT ts_ms, ts_iso, td_area, msg_type, address, data FROM td_signal_events ORDER BY ts_ms DESC LIMIT 500")
        body = ["<h2>TD Signal Events (S-Class)</h2>"]
        body.append("<table><tr><th>Time</th><th>Area</th><th>Type</th><th>Address</th><th>Data</th></tr>")
        for r in rows:
            body.append(f"<tr><td class='mono'>{r['ts_iso']}</td><td>{r['td_area']}</td><td>{r['msg_type']}</td><td>{r['address']}</td><td>{r['data'] if r['data'] else ''}</td></tr>")
        body.append("</table>")
        return render_page("Signals - NR RailHub", body, active="signals")

    @app.get("/raw-events")
    def raw_events():
        msg_type = request.args.get("msg_type", "").strip()
        area = request.args.get("area", "").strip()

        body = ["<h2>Raw TD Events</h2>"]
        body.append("<p>This page shows combined berth and signal events.</p>")
        
        # Query both berth and signal events
        berth_sql = "SELECT ts_ms, ts_iso, td_area, headcode, msg_type, from_berth, to_berth, NULL as address, NULL as data FROM td_berth_events WHERE 1=1"
        signal_sql = "SELECT ts_ms, ts_iso, td_area, NULL as headcode, msg_type, NULL as from_berth, NULL as to_berth, address, data FROM td_signal_events WHERE 1=1"
        
        params = []
        if msg_type:
            berth_sql += " AND msg_type=?"
            signal_sql += " AND msg_type=?"
            params.append(msg_type)
            params.append(msg_type)
        if area:
            berth_sql += " AND td_area=?"
            signal_sql += " AND td_area=?"
            params.append(area)
            params.append(area)
        
        combined_sql = f"SELECT * FROM ({berth_sql} UNION ALL {signal_sql}) ORDER BY ts_ms DESC LIMIT 500"
        
        try:
            rows = q(combined_sql, params)
            if rows:
                # table header from row keys
                keys = rows[0].keys()
                body.append("<table><tr>" + "".join(f"<th>{k}</th>" for k in keys) + "</tr>")
                for r in rows:
                    row_data = [str(r[k]) if r[k] is not None else '' for k in keys]
                    body.append("<tr>" + "".join([f"<td class='mono'>{d}</td>" for d in row_data]) + "</tr>")
                body.append("</table>")
                body.append(f"<p class='dim'>Showing {len(rows)} event(s) from combined berth and signal tables</p>")
            else:
                body.append("<p><i>No events matching filters</i></p>")
        except Exception as e:
            body.append(f"<p><i>Error querying events: {e}</i></p>")
        return render_page("Raw Events - NR RailHub", body, active="raw")

    @app.get("/stats")
    def stats():
        body = ["<h2>Stats</h2>"]
        try:
            counts = q("""
                SELECT 
                    (SELECT COUNT(*) FROM td_state) AS td_state,
                    (SELECT COUNT(*) FROM td_berth_events) AS td_berth_events,
                    (SELECT COUNT(*) FROM td_signal_events) AS td_signal_events
            """)[0]
            body.append(f"<p class='dim'>td_state={counts['td_state']} td_berth_events={counts['td_berth_events']} td_signal_events={counts['td_signal_events']}</p>")
        except Exception as e:
            body.append(f"<p><i>Error fetching stats: {e}</i></p>")
        return render_page("Stats - NR RailHub", body, active="stats")

    @app.route("/mapper", methods=["GET", "POST"])
    def mapper():
        """Mapper configuration and rebuild page."""
        body = ["<h2>Berth-Signal Mapper Configuration</h2>"]
        
        # Check if mapper tables exist
        try:
            table_check = q("SELECT name FROM sqlite_master WHERE type='table' AND name='mapper_config'")
            if not table_check:
                body.append("<p><i>Mapper tables not found. Ensure database was created with mapper support enabled.</i></p>")
                return render_page("Mapper - NR RailHub", body, active="mapper")
        except Exception as e:
            body.append(f"<p><i>Error checking mapper tables: {e}</i></p>")
            return render_page("Mapper - NR RailHub", body, active="mapper")
        
        # Handle POST (rebuild request)
        if request.method == "POST":
            try:
                pre_ms = int(request.form.get("pre_ms", 1000))
                post_ms = int(request.form.get("post_ms", 5000))
                tau_ms = int(request.form.get("tau_ms", 2500))
                save_config = request.form.get("save_config") == "yes"
                td_area_filter = request.form.get("td_area", "").strip() or None
                
                # Validate parameters
                if pre_ms < 0 or pre_ms > 60000:
                    body.append("<p style='color:red'>Error: pre_ms must be between 0 and 60000</p>")
                elif post_ms < 0 or post_ms > 60000:
                    body.append("<p style='color:red'>Error: post_ms must be between 0 and 60000</p>")
                elif tau_ms < 1 or tau_ms > 60000:
                    body.append("<p style='color:red'>Error: tau_ms must be between 1 and 60000</p>")
                else:
                    # Save configuration if requested
                    if save_config:
                        cursor = conn.cursor()
                        cursor.execute("UPDATE mapper_config SET value=?, updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE key='pre_ms'", (pre_ms,))
                        cursor.execute("UPDATE mapper_config SET value=?, updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE key='post_ms'", (post_ms,))
                        cursor.execute("UPDATE mapper_config SET value=?, updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE key='tau_ms'", (tau_ms,))
                        conn.commit()
                        body.append("<p style='color:green'><b>Configuration saved successfully</b></p>")
                    
                    # Perform rebuild
                    body.append(f"<p><b>Rebuilding mapper scores...</b></p>")
                    body.append(f"<p class='dim'>Parameters: pre_ms={pre_ms}, post_ms={post_ms}, tau_ms={tau_ms}</p>")
                    if td_area_filter:
                        body.append(f"<p class='dim'>Area filter: {td_area_filter}</p>")
                    else:
                        body.append(f"<p class='dim'>Rebuilding all areas</p>")
                    
                    progress_messages = []
                    
                    # Import mapper function
                    from .mapper import process_batch_for_mapper
                    
                    cursor = conn.cursor()
                    
                    # Get list of areas to process
                    if td_area_filter:
                        areas = [td_area_filter]
                    else:
                        cursor.execute("SELECT DISTINCT td_area FROM berth_signal_observations WHERE td_area IS NOT NULL ORDER BY td_area")
                        areas = [row[0] for row in cursor.fetchall()]
                    
                    progress_messages.append(f"Starting rebuild for {len(areas)} area(s)")
                    
                    # Clear existing scores for the selected area(s)
                    if td_area_filter:
                        cursor.execute("DELETE FROM berth_signal_scores WHERE td_area=?", (td_area_filter,))
                    else:
                        cursor.execute("DELETE FROM berth_signal_scores")
                    deleted = cursor.rowcount
                    progress_messages.append(f"Cleared {deleted} existing score entries")
                    
                    total_observations = 0
                    total_inserted = 0
                    
                    for area in areas:
                        progress_messages.append(f"Processing area: {area}")
                        
                        # Fetch all observations for this area
                        cursor.execute("""
                            SELECT 
                                td_area, step_timestamp, from_berth, to_berth, descr,
                                signal_timestamp, address, data
                            FROM berth_signal_observations
                            WHERE td_area=?
                            ORDER BY step_timestamp
                        """, (area,))
                        
                        obs_rows = cursor.fetchall()
                        total_observations += len(obs_rows)
                        progress_messages.append(f"  Found {len(obs_rows)} observations")
                        
                        # Convert to event format for reprocessing
                        from datetime import datetime, timezone
                        events = []
                        for row in obs_rows:
                            td_area_val = row[0]
                            step_ts = row[1]
                            from_b = row[2]
                            to_b = row[3]
                            descr = row[4]
                            sig_ts = row[5]
                            addr = row[6]
                            data = row[7]
                            
                            # Add step event
                            if step_ts and from_b and to_b:
                                # Convert timestamp to ISO format for received_at_utc
                                try:
                                    step_dt = datetime.fromtimestamp(step_ts / 1000.0, tz=timezone.utc)
                                    step_utc = step_dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                                except Exception:
                                    step_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                                
                                events.append({
                                    'msg_ts': step_ts,
                                    'msg_type': 'CA',
                                    'td_area': td_area_val,
                                    'from_berth': from_b,
                                    'to_berth': to_b,
                                    'descr': descr,
                                    'address': None,
                                    'data': None,
                                    'received_at_utc': step_utc
                                })
                            
                            # Add signal event
                            if sig_ts and addr:
                                # Convert timestamp to ISO format for received_at_utc
                                try:
                                    sig_dt = datetime.fromtimestamp(sig_ts / 1000.0, tz=timezone.utc)
                                    sig_utc = sig_dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                                except Exception:
                                    sig_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                                
                                events.append({
                                    'msg_ts': sig_ts,
                                    'msg_type': 'SF',
                                    'td_area': td_area_val,
                                    'from_berth': None,
                                    'to_berth': None,
                                    'descr': None,
                                    'address': addr,
                                    'data': data,
                                    'received_at_utc': sig_utc
                                })
                        
                        # Deduplicate events by key
                        seen = set()
                        unique_events = []
                        for e in events:
                            key = (e['msg_type'], e['msg_ts'], e.get('address'), e.get('from_berth'), e.get('to_berth'))
                            if key not in seen:
                                seen.add(key)
                                unique_events.append(e)
                        
                        if unique_events:
                            # Reprocess with new parameters
                            _, score_rows = process_batch_for_mapper(
                                unique_events,
                                pre_ms=pre_ms,
                                post_ms=post_ms,
                                tau_ms=tau_ms
                            )
                            
                            if score_rows:
                                # Insert new scores (accumulate if duplicate)
                                cursor.executemany("""
                                    INSERT INTO berth_signal_scores (
                                        td_area, from_berth, to_berth, address, score, last_seen_ts, last_seen_utc, last_data
                                    )
                                    VALUES (?,?,?,?,?,?,?,?)
                                    ON CONFLICT(td_area, from_berth, to_berth, address)
                                    DO UPDATE SET
                                        score = score + excluded.score,
                                        obs_count = obs_count + 1,
                                        last_seen_ts = CASE WHEN excluded.last_seen_ts > last_seen_ts THEN excluded.last_seen_ts ELSE last_seen_ts END,
                                        last_seen_utc = CASE WHEN excluded.last_seen_ts > last_seen_ts THEN excluded.last_seen_utc ELSE last_seen_utc END,
                                        last_data = CASE WHEN excluded.last_seen_ts > last_seen_ts THEN excluded.last_data ELSE last_data END
                                """, score_rows)
                                
                                total_inserted += len(score_rows)
                                progress_messages.append(f"  Generated {len(score_rows)} score entries")
                    
                    conn.commit()
                    progress_messages.append(f"Rebuild complete: processed {total_observations} observations, generated {total_inserted} score entries")
                    
                    body.append("<div style='background:#f7f9fc;padding:10px;border-radius:6px;margin:10px 0'>")
                    for msg in progress_messages:
                        body.append(f"<p class='dim' style='margin:4px 0'>{msg}</p>")
                    body.append("</div>")
                    
                    body.append(f"<p style='color:green'><b>Rebuild complete!</b></p>")
                    body.append(f"<p>Deleted: {deleted} | Inserted: {total_inserted} | Observations: {total_observations}</p>")
                    
            except Exception as e:
                body.append(f"<p style='color:red'>Error during rebuild: {e}</p>")
                import traceback
                body.append(f"<pre style='font-size:11px;background:#f7f9fc;padding:8px'>{traceback.format_exc()}</pre>")
        
        # Get current configuration
        try:
            config = q("SELECT key, value FROM mapper_config")
            current_config = {row[0]: row[1] for row in config}
            pre_ms_current = current_config.get("pre_ms", 1000)
            post_ms_current = current_config.get("post_ms", 5000)
            tau_ms_current = current_config.get("tau_ms", 2500)
        except Exception:
            pre_ms_current = 1000
            post_ms_current = 5000
            tau_ms_current = 2500
        
        # Get mapper statistics
        try:
            obs_count = q("SELECT COUNT(*) as cnt FROM berth_signal_observations")[0]["cnt"]
            score_count = q("SELECT COUNT(*) as cnt FROM berth_signal_scores")[0]["cnt"]
            areas = q("SELECT COUNT(DISTINCT td_area) as cnt FROM berth_signal_observations")[0]["cnt"]
        except Exception:
            obs_count = 0
            score_count = 0
            areas = 0
        
        body.append("<h3>Current Configuration</h3>")
        body.append(f"<p><b>pre_ms:</b> {pre_ms_current} (time window before step event)</p>")
        body.append(f"<p><b>post_ms:</b> {post_ms_current} (time window after step event)</p>")
        body.append(f"<p><b>tau_ms:</b> {tau_ms_current} (exponential weighting decay constant)</p>")
        
        body.append("<h3>Mapper Statistics</h3>")
        body.append(f"<p><b>Observations:</b> {obs_count}</p>")
        body.append(f"<p><b>Score Entries:</b> {score_count}</p>")
        body.append(f"<p><b>TD Areas:</b> {areas}</p>")
        
        body.append("<h3>Rebuild Mapper Scores</h3>")
        body.append("""
            <p class='dim'>
            Adjust the mapper parameters below and rebuild the berth-signal correlation scores.
            The rebuild will reprocess all existing observations with the new parameters.
            </p>
        """)
        
        body.append("""
            <form method='post' style='background:#f7f9fc;padding:15px;border-radius:6px'>
                <div style='margin-bottom:12px'>
                    <label style='display:inline-block;width:150px;font-weight:600'>pre_ms:</label>
                    <input type='number' name='pre_ms' value='""" + str(pre_ms_current) + """' min='0' max='60000' required style='padding:6px;width:100px'>
                    <span class='dim' style='margin-left:8px'>Time window before step (ms)</span>
                </div>
                <div style='margin-bottom:12px'>
                    <label style='display:inline-block;width:150px;font-weight:600'>post_ms:</label>
                    <input type='number' name='post_ms' value='""" + str(post_ms_current) + """' min='0' max='60000' required style='padding:6px;width:100px'>
                    <span class='dim' style='margin-left:8px'>Time window after step (ms)</span>
                </div>
                <div style='margin-bottom:12px'>
                    <label style='display:inline-block;width:150px;font-weight:600'>tau_ms:</label>
                    <input type='number' name='tau_ms' value='""" + str(tau_ms_current) + """' min='1' max='60000' required style='padding:6px;width:100px'>
                    <span class='dim' style='margin-left:8px'>Exponential weight decay (ms)</span>
                </div>
                <div style='margin-bottom:12px'>
                    <label style='display:inline-block;width:150px;font-weight:600'>TD Area Filter:</label>
                    <input type='text' name='td_area' value='' placeholder='(all areas)' style='padding:6px;width:100px'>
                    <span class='dim' style='margin-left:8px'>Optional: rebuild only this area</span>
                </div>
                <div style='margin-bottom:12px'>
                    <label style='display:inline-block;width:150px;font-weight:600'>Save Config:</label>
                    <input type='checkbox' name='save_config' value='yes' checked>
                    <span class='dim' style='margin-left:8px'>Save these parameters as defaults</span>
                </div>
                <div style='margin-top:16px'>
                    <button type='submit' style='padding:8px 16px;background:#0b5cff;color:white;border:0;border-radius:6px;font-weight:600;cursor:pointer'>Rebuild Scores</button>
                </div>
            </form>
        """)
        
        body.append("""
            <h3>Parameter Explanations</h3>
            <ul>
                <li><b>pre_ms</b>: Milliseconds to look back before a berth step event when correlating with signal events</li>
                <li><b>post_ms</b>: Milliseconds to look forward after a berth step event when correlating with signal events</li>
                <li><b>tau_ms</b>: Time constant for exponential weighting - smaller values favor closer time matches</li>
            </ul>
            <p class='dim'>
            The mapper uses these parameters to correlate berth step movements (CA/CB/CC) with signal events (SF).
            Adjusting these values affects the confidence scores for berth-to-signal mappings.
            </p>
        """)
        
        return render_page("Mapper - NR RailHub", body, active="mapper")

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

