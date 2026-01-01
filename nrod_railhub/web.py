#!/usr/bin/env python3
"""Web dashboard for nrod_railhub."""

from __future__ import annotations

import pathlib
import sqlite3

from flask import Flask, request

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
                ".pill{display:inline-block;padding:2px 8px;border-radius:999px;background:#eee;margin-right:6px}</style>"
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
        html.append("<p><a href='/events'>Recent events</a></p>")
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

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

