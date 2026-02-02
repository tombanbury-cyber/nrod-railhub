#!/usr/bin/env python3
"""Web dashboard for nrod_railhub."""

from __future__ import annotations

import os
import pathlib
import sqlite3

from flask import Flask, request


def hex_to_bits(hex_str: str) -> str:
    """Convert hex string to binary representation."""
    try:
        b = int(hex_str, 16)
        return format(b, "08b")  # 8 bits
    except Exception:
        return ""


def start_web_dashboard(db_path: str, port: int) -> None:
    app = Flask(__name__)
    db_path = str(pathlib.Path(db_path).expanduser())
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000;")

    def q(sql: str, params=()):
        cur = conn.execute(sql, params)
        return cur.fetchall()

    @app.get("/")
    def index():

        counts = q("SELECT (SELECT COUNT(*) FROM td_state) AS td_state, (SELECT COUNT(*) FROM td_event) AS td_event, (SELECT COUNT(*) FROM trust_state) AS trust_state, (SELECT COUNT(*) FROM vstp_state) AS vstp_state")[0]

        area = request.args.get("area", "").strip()
        if area:
            rows = q("SELECT * FROM td_state WHERE td_area=? ORDER BY last_time_utc DESC LIMIT 200", (area,))
        else:
            rows = q("SELECT * FROM td_state ORDER BY last_time_utc DESC LIMIT 200")
        areas = [r[0] for r in q("SELECT DISTINCT td_area FROM td_state ORDER BY td_area")]
        html = ["<html><head><meta charset='utf-8'><title>NR RailHub</title>"
                "<style>body{font-family:system-ui,Arial;margin:20px} table{border-collapse:collapse;width:100%}"
                "th,td{border-bottom:1px solid #ddd;padding:6px 8px;font-size:14px} th{text-align:left}"
                ".pill{display:inline-block;padding:2px 8px;border-radius:999px;background:#eee;margin-right:6px}"
                ".conf-high{color:#28a745} .conf-med{color:#fd7e14} .conf-low{color:#6c757d}"
                ".mono{font-family:monospace} .dim{color:#6c757d;font-size:12px}"
                ".nav-links{margin:20px 0;padding:10px;background:#f8f9fa;border-radius:5px}"
                ".nav-links a{margin-right:15px;text-decoration:none;color:#007bff}"
                ".nav-links a:hover{text-decoration:underline}</style>"
                "</head><body>"]
        html.append("<h2>NR RailHub</h2>")
        html.append(f"<p><b>DB:</b> td_state={counts['td_state']} td_event={counts['td_event']} trust_state={counts['trust_state']} vstp_state={counts['vstp_state']}</p>")
        html.append("<div>Filter: " + " ".join([f"<a class='pill' href='/?area={a}'>{a}</a>" for a in areas]) + " <a class='pill' href='/'>ALL</a></div>")
        html.append("<h3>Latest TD state" + (f" (area {area})" if area else "") + "</h3>")        
        html.append("<table><tr><th>Area</th><th>Headcode</th><th>Time</th><th>From</th><th>To</th><th>Location</th><th>Plat</th><th>Sched</th></tr>")
        for r in rows:
            sched = ""
            if r["sched_dep"] or r["sched_arr"]:
                sched = f"{r['sched_dep'] or ''}→{r['sched_arr'] or ''} {r['origin_name'] or ''}→{r['dest_name'] or ''}"
            loc = r["location_name"] or ""
            if r["stanox"]:
                loc = f"{loc} ({r['stanox']})".strip()
            html.append(f"<tr><td>{r['td_area']}</td><td><a href='/train?area={r['td_area']}&hc={r['headcode']}'>{r['headcode']}</a></td><td>{r['last_time_utc'] or ''}</td><td>{r['from_berth'] or ''}</td><td>{r['to_berth'] or ''}</td><td>{loc}</td><td>{r['platform'] or ''}</td><td>{sched}</td></tr>")
        html.append("</table>")
        html.append("<div class='nav-links'><b>Views:</b> <a href='/events'>Recent Events</a> | <a href='/signals'>Signal Mapper</a> | <a href='/raw-events'>Raw Events</a> | <a href='/stats'>Stats</a></div>")
        html.append("</body></html>")
        return "\n".join(html)

    @app.get("/train")
    def train():
        area = request.args.get("area","")
        hc = request.args.get("hc","")
        st = q("SELECT * FROM td_state WHERE td_area=? AND headcode=?", (area, hc))
        ev = q("SELECT * FROM td_event WHERE td_area=? AND headcode=? ORDER BY ts_utc DESC LIMIT 200", (area, hc))
        html=["<html><head><meta charset='utf-8'><title>Train</title></head><body style='font-family:system-ui,Arial;margin:20px'>"]
        html.append(f"<h2>{area} / {hc}</h2><p><a href='/'>Back</a></p>")
        if st:
            r=st[0]
            html.append("<pre>"+str(dict(r))+"</pre>")
        html.append("<h3>Recent events</h3><table><tr><th>Time</th><th>Type</th><th>From</th><th>To</th></tr>")
        for r in ev:
            html.append(f"<tr><td>{r['ts_utc']}</td><td>{r['event_type']}</td><td>{r['from_berth'] or ''}</td><td>{r['to_berth'] or ''}</td></tr>")
        html.append("</table></body></html>")
        return "\n".join(html)

    @app.get("/events")
    def events():
        rows = q("SELECT ts_utc, td_area, headcode, event_type, from_berth, to_berth FROM td_event ORDER BY ts_utc DESC LIMIT 500")
        html=["<html><head><meta charset='utf-8'><title>Events</title></head><body style='font-family:system-ui,Arial;margin:20px'>"]
        html.append("<h2>Recent TD events</h2><p><a href='/'>Back</a></p>")
        html.append("<table><tr><th>Time</th><th>Area</th><th>Headcode</th><th>Type</th><th>From</th><th>To</th></tr>")
        for r in rows:
            html.append(f"<tr><td>{r['ts_utc']}</td><td>{r['td_area']}</td><td>{r['headcode']}</td><td>{r['event_type']}</td><td>{r['from_berth']}</td><td>{r['to_berth']}</td></tr>")
        html.append("</table></body></html>")
        return "\n".join(html)

    @app.get("/signals")
    def signals():
        """Signal Mapper View - shows berth to signal address mappings with confidence scores."""
        area = request.args.get("area", "").strip()
        
        html = ["<html><head><meta charset='utf-8'><title>Signal Mapper</title>"
                "<style>body{font-family:system-ui,Arial;margin:20px} table{border-collapse:collapse;width:100%}"
                "th,td{border-bottom:1px solid #ddd;padding:6px 8px;font-size:14px} th{text-align:left}"
                ".pill{display:inline-block;padding:2px 8px;border-radius:999px;background:#eee;margin-right:6px}"
                ".conf-high{color:#28a745;font-weight:bold} .conf-med{color:#fd7e14} .conf-low{color:#6c757d}"
                ".mono{font-family:monospace;font-size:12px} .dim{color:#6c757d;font-size:12px}</style>"
                "</head><body>"]
        html.append("<h2>Signal Mapper</h2>")
        html.append("<p><a href='/'>Back</a></p>")
        
        # Check if table exists
        try:
            table_check = q("SELECT name FROM sqlite_master WHERE type='table' AND name='berth_signal_scores'")
            if not table_check:
                html.append("<p><i>berth_signal_scores table not found. This feature requires the experimental TD feed dashboard to populate signal data.</i></p>")
                html.append("</body></html>")
                return "\n".join(html)
            
            # Get distinct areas for filter
            areas = [r[0] for r in q("SELECT DISTINCT td_area FROM berth_signal_scores ORDER BY td_area")]
            html.append("<div>Filter: " + " ".join([f"<a class='pill' href='/signals?area={a}'>{a}</a>" for a in areas]) + " <a class='pill' href='/signals'>ALL</a></div>")
            html.append("<h3>Berth Signal Mappings" + (f" (area {area})" if area else "") + "</h3>")
            
            # Query berth_signal_scores
            if area:
                rows = q("""SELECT td_area, from_berth, to_berth, address, score, obs_count, 
                            last_seen_ts, last_seen_utc, last_data 
                            FROM berth_signal_scores 
                            WHERE td_area=? 
                            ORDER BY score DESC LIMIT 500""", (area,))
            else:
                rows = q("""SELECT td_area, from_berth, to_berth, address, score, obs_count, 
                            last_seen_ts, last_seen_utc, last_data 
                            FROM berth_signal_scores 
                            ORDER BY score DESC LIMIT 500""")
            
            if not rows:
                html.append("<p><i>No signal mapping data available yet.</i></p>")
            else:
                html.append("<table><tr><th>TD Area</th><th>From Berth</th><th>To Berth</th><th>Address</th><th>Score</th><th>Obs Count</th><th>Confidence</th><th>Last Seen</th><th>Last Data</th></tr>")
                for r in rows:
                    score = r['score'] if r['score'] else 0.0
                    obs_count = r['obs_count'] if r['obs_count'] else 0
                    
                    # Calculate confidence as percentage
                    conf_pct = int(round(score * 100))
                    
                    # Determine confidence class
                    if score >= 0.90:
                        conf_class = "conf-high"
                        conf_label = "HIGH"
                    elif score >= 0.70:
                        conf_class = "conf-med"
                        conf_label = "MED"
                    else:
                        conf_class = "conf-low"
                        conf_label = "LOW"
                    
                    # Convert hex data to binary if present
                    last_data = r['last_data'] or ''
                    binary_data = ''
                    if last_data:
                        binary_data = hex_to_bits(last_data)
                        if binary_data:
                            last_data = f"{last_data} ({binary_data})"
                    
                    last_seen = r['last_seen_utc'] or ''
                    
                    html.append(f"<tr><td>{r['td_area']}</td>"
                               f"<td>{r['from_berth'] or ''}</td>"
                               f"<td>{r['to_berth'] or ''}</td>"
                               f"<td class='mono'>{r['address']}</td>"
                               f"<td>{score:.3f}</td>"
                               f"<td>{obs_count}</td>"
                               f"<td class='{conf_class}'>{conf_label} ({conf_pct}%)</td>"
                               f"<td class='dim'>{last_seen}</td>"
                               f"<td class='mono dim'>{last_data}</td></tr>")
                html.append("</table>")
                html.append(f"<p class='dim'>Showing {len(rows)} mapping(s)</p>")
        
        except Exception as e:
            html.append(f"<p><i>Error querying signal mapper data: {e}</i></p>")
        
        html.append("</body></html>")
        return "\n".join(html)

    @app.get("/raw-events")
    def raw_events():
        """Raw Events View - filterable TD events table."""
        msg_type = request.args.get("msg_type", "").strip()
        area = request.args.get("area", "").strip()
        
        html = ["<html><head><meta charset='utf-8'><title>Raw Events</title>"
                "<style>body{font-family:system-ui,Arial;margin:20px} table{border-collapse:collapse;width:100%}"
                "th,td{border-bottom:1px solid #ddd;padding:6px 8px;font-size:14px} th{text-align:left}"
                ".pill{display:inline-block;padding:2px 8px;border-radius:999px;background:#eee;margin-right:6px;margin-bottom:5px}"
                ".pill.active{background:#007bff;color:white}"
                ".mono{font-family:monospace;font-size:12px} .dim{color:#6c757d;font-size:12px}</style>"
                "</head><body>"]
        html.append("<h2>Raw TD Events</h2>")
        html.append("<p><a href='/'>Back</a></p>")
        
        # Try td_events first (experimental dashboard), fallback to td_event (main app)
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
            html.append("<p><i>No TD events table found (td_events or td_event).</i></p>")
            html.append("</body></html>")
            return "\n".join(html)
        
        try:
            # Determine column names based on table
            if table_name == "td_events":
                timestamp_col = "received_at_utc"
                descr_col = "descr"
                has_address = True
                has_data = True
            else:  # td_event
                timestamp_col = "ts_utc"
                descr_col = "headcode"
                # Check if address/data columns exist
                cols_check = q(f"PRAGMA table_info({table_name})")
                col_names = [c[1] for c in cols_check]
                has_address = "address" in col_names
                has_data = "data" in col_names
            
            # Get distinct message types and areas for filters
            msg_types = [r[0] for r in q(f"SELECT DISTINCT msg_type FROM {table_name} WHERE msg_type IS NOT NULL ORDER BY msg_type") if r[0]]
            areas = [r[0] for r in q(f"SELECT DISTINCT td_area FROM {table_name} WHERE td_area IS NOT NULL ORDER BY td_area") if r[0]]
            
            # Filter controls
            html.append("<div><b>Message Type:</b> ")
            common_types = ["CA", "CB", "CC", "SF"]
            for mt in common_types:
                active_class = " active" if mt == msg_type else ""
                html.append(f"<a class='pill{active_class}' href='/raw-events?msg_type={mt}{'&area=' + area if area else ''}'>{mt}</a>")
            html.append(f"<a class='pill{' active' if not msg_type else ''}' href='/raw-events{'?area=' + area if area else ''}'>ALL</a>")
            html.append("</div>")
            
            html.append("<div style='margin-top:10px'><b>TD Area:</b> ")
            for a in areas[:20]:  # Limit to first 20 areas
                active_class = " active" if a == area else ""
                html.append(f"<a class='pill{active_class}' href='/raw-events?area={a}{'&msg_type=' + msg_type if msg_type else ''}'>{a}</a>")
            html.append(f"<a class='pill{' active' if not area else ''}' href='/raw-events{'?msg_type=' + msg_type if msg_type else ''}'>ALL</a>")
            html.append("</div>")
            
            # Build query
            where_clauses = []
            params = []
            if msg_type:
                where_clauses.append("msg_type=?")
                params.append(msg_type)
            if area:
                where_clauses.append("td_area=?")
                params.append(area)
            
            where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
            
            # Query events
            if table_name == "td_events":
                if has_address and has_data:
                    sql = f"SELECT {timestamp_col}, td_area, msg_type, {descr_col}, from_berth, to_berth, address, data FROM {table_name}{where_sql} ORDER BY id DESC LIMIT 500"
                else:
                    sql = f"SELECT {timestamp_col}, td_area, msg_type, {descr_col}, from_berth, to_berth FROM {table_name}{where_sql} ORDER BY id DESC LIMIT 500"
            else:  # td_event
                sql = f"SELECT {timestamp_col}, td_area, event_type AS msg_type, {descr_col}, from_berth, to_berth FROM {table_name}{where_sql} ORDER BY {timestamp_col} DESC LIMIT 500"
            
            rows = q(sql, tuple(params))
            
            filters_desc = []
            if msg_type:
                filters_desc.append(f"type={msg_type}")
            if area:
                filters_desc.append(f"area={area}")
            filter_str = " (" + ", ".join(filters_desc) + ")" if filters_desc else ""
            
            html.append(f"<h3>Recent Events{filter_str}</h3>")
            
            if not rows:
                html.append("<p><i>No events found.</i></p>")
            else:
                # Table header
                header_cols = ["Timestamp", "Area", "Msg Type", "Descr", "From Berth", "To Berth"]
                if table_name == "td_events" and has_address:
                    header_cols.append("Address")
                if table_name == "td_events" and has_data:
                    header_cols.append("Data")
                
                html.append("<table><tr>" + "".join([f"<th>{h}</th>" for h in header_cols]) + "</tr>")
                
                for r in rows:
                    row_data = [
                        r[0] or '',  # timestamp
                        r[1] or '',  # area
                        r[2] or '',  # msg_type
                        r[3] or '',  # descr/headcode
                        r[4] or '',  # from_berth
                        r[5] or '',  # to_berth
                    ]
                    
                    if table_name == "td_events":
                        if has_address and len(r) > 6:
                            row_data.append(r[6] or '')  # address
                        if has_data and len(r) > 7:
                            row_data.append(r[7] or '')  # data
                    
                    html.append("<tr>" + "".join([f"<td class='{'mono' if i >= len(row_data)-2 else ''}'>{d}</td>" for i, d in enumerate(row_data)]) + "</tr>")
                
                html.append("</table>")
                html.append(f"<p class='dim'>Showing {len(rows)} event(s) from {table_name} table</p>")
        
        except Exception as e:
            html.append(f"<p><i>Error querying events: {e}</i></p>")
        
        html.append("</body></html>")
        return "\n".join(html)

    @app.get("/stats")
    def stats():
        """Enhanced Stats View - database statistics and health information."""
        html = ["<html><head><meta charset='utf-8'><title>Database Stats</title>"
                "<style>body{font-family:system-ui,Arial;margin:20px} table{border-collapse:collapse;width:100%;margin-bottom:20px}"
                "th,td{border-bottom:1px solid #ddd;padding:6px 8px;font-size:14px} th{text-align:left}"
                ".stat-section{margin-bottom:30px} .dim{color:#6c757d;font-size:12px}"
                "h3{margin-top:20px;margin-bottom:10px;color:#333}</style>"
                "</head><body>"]
        html.append("<h2>Database Statistics</h2>")
        html.append("<p><a href='/'>Back</a></p>")
        
        # Database file size
        try:
            db_size = os.path.getsize(db_path)
            db_size_mb = db_size / (1024 * 1024)
            html.append(f"<p><b>Database File:</b> {db_path}</p>")
            html.append(f"<p><b>File Size:</b> {db_size_mb:.2f} MB ({db_size:,} bytes)</p>")
        except Exception as e:
            html.append(f"<p><b>Database File:</b> {db_path} (size unavailable: {e})</p>")
        
        # Table row counts
        html.append("<div class='stat-section'>")
        html.append("<h3>Table Row Counts</h3>")
        html.append("<table><tr><th>Table</th><th>Row Count</th></tr>")
        
        tables_to_check = [
            "td_events",
            "td_event", 
            "td_state",
            "trust_state",
            "vstp_state",
            "berth_signal_scores",
            "berth_signal_observations"
        ]
        
        for table in tables_to_check:
            try:
                table_check = q(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
                if table_check:
                    count = q(f"SELECT COUNT(*) as cnt FROM {table}")[0]['cnt']
                    html.append(f"<tr><td>{table}</td><td>{count:,}</td></tr>")
                else:
                    html.append(f"<tr><td>{table}</td><td class='dim'>N/A (table not found)</td></tr>")
            except Exception as e:
                html.append(f"<tr><td>{table}</td><td class='dim'>Error: {e}</td></tr>")
        
        html.append("</table>")
        html.append("</div>")
        
        # Message type distribution
        html.append("<div class='stat-section'>")
        html.append("<h3>Message Type Distribution</h3>")
        
        # Try td_events first, fallback to td_event
        msg_type_table = None
        try:
            table_check = q("SELECT name FROM sqlite_master WHERE type='table' AND name='td_events'")
            if table_check:
                msg_type_table = "td_events"
            else:
                table_check = q("SELECT name FROM sqlite_master WHERE type='table' AND name='td_event'")
                if table_check:
                    msg_type_table = "td_event"
        except Exception:
            pass
        
        if msg_type_table:
            try:
                if msg_type_table == "td_event":
                    rows = q(f"SELECT event_type AS msg_type, COUNT(*) as cnt FROM {msg_type_table} WHERE event_type IS NOT NULL GROUP BY event_type ORDER BY cnt DESC")
                else:
                    rows = q(f"SELECT msg_type, COUNT(*) as cnt FROM {msg_type_table} WHERE msg_type IS NOT NULL GROUP BY msg_type ORDER BY cnt DESC")
                
                if rows:
                    html.append("<table><tr><th>Message Type</th><th>Count</th></tr>")
                    for r in rows:
                        html.append(f"<tr><td>{r[0]}</td><td>{r[1]:,}</td></tr>")
                    html.append("</table>")
                else:
                    html.append("<p class='dim'>No message type data available.</p>")
            except Exception as e:
                html.append(f"<p class='dim'>Error: {e}</p>")
        else:
            html.append("<p class='dim'>N/A (no events table found)</p>")
        
        html.append("</div>")
        
        # TD Area distribution
        html.append("<div class='stat-section'>")
        html.append("<h3>TD Area Distribution</h3>")
        
        if msg_type_table:
            try:
                rows = q(f"SELECT td_area, COUNT(*) as cnt FROM {msg_type_table} WHERE td_area IS NOT NULL GROUP BY td_area ORDER BY cnt DESC LIMIT 50")
                if rows:
                    html.append("<table><tr><th>TD Area</th><th>Event Count</th></tr>")
                    for r in rows:
                        html.append(f"<tr><td>{r[0]}</td><td>{r[1]:,}</td></tr>")
                    html.append("</table>")
                else:
                    html.append("<p class='dim'>No TD area data available.</p>")
            except Exception as e:
                html.append(f"<p class='dim'>Error: {e}</p>")
        else:
            html.append("<p class='dim'>N/A (no events table found)</p>")
        
        html.append("</div>")
        
        # Recent activity
        html.append("<div class='stat-section'>")
        html.append("<h3>Recent Activity (Last 10 Events)</h3>")
        
        if msg_type_table:
            try:
                if msg_type_table == "td_event":
                    rows = q(f"SELECT ts_utc, event_type AS msg_type, td_area, headcode FROM {msg_type_table} ORDER BY ts_utc DESC LIMIT 10")
                    html.append("<table><tr><th>Timestamp</th><th>Type</th><th>Area</th><th>Headcode</th></tr>")
                else:
                    rows = q(f"SELECT received_at_utc, msg_type, td_area, descr FROM {msg_type_table} ORDER BY id DESC LIMIT 10")
                    html.append("<table><tr><th>Timestamp</th><th>Type</th><th>Area</th><th>Descr</th></tr>")
                
                for r in rows:
                    html.append(f"<tr><td>{r[0] or ''}</td><td>{r[1] or ''}</td><td>{r[2] or ''}</td><td>{r[3] or ''}</td></tr>")
                html.append("</table>")
            except Exception as e:
                html.append(f"<p class='dim'>Error: {e}</p>")
        else:
            html.append("<p class='dim'>N/A (no events table found)</p>")
        
        html.append("</div>")
        html.append("</body></html>")
        return "\n".join(html)

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

