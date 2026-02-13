#!/usr/bin/env python3
"""
Flask-based lightweight web dashboard for nrod-railhub.

This file refactors route rendering to use a shared header/footer and adds
a persistent top navigation bar with a quick filter form. Styles are kept
simple and inline to avoid adding new dependencies.
"""

from __future__ import annotations

import time
import json
import yaml
import logging
import queue
from datetime import datetime
import html
from urllib.parse import urlencode

import pathlib
import sqlite3
from typing import List, Dict, Any, Optional

from flask import Flask, request, redirect

from .logging_config import get_logger

logger = get_logger("web")

def start_web_dashboard(db_path: str, port: int, config_path: Optional[str] = None, log_queue: Optional["queue.Queue[str]"] = None) -> None:
    app = Flask(__name__)
    db_path = str(pathlib.Path(db_path).expanduser())
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000;")
    
    # Store config path for reading/writing
    _config_path = str(pathlib.Path(config_path).expanduser()) if config_path else None
    
    def load_yaml_config() -> Dict[str, Any]:
        """Load configuration from YAML file."""
        if not _config_path or not pathlib.Path(_config_path).exists():
            return {}
        try:
            with open(_config_path, 'r') as f:
                config = yaml.safe_load(f)
                return config if isinstance(config, dict) else {}
        except Exception as e:
            logger.error(f"Error loading config from {_config_path}: {e}")
            return {}
    
    def save_yaml_config(config: Dict[str, Any]) -> bool:
        """Save configuration to YAML file."""
        if not _config_path:
            return False
        try:
            with open(_config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)
            logger.info(f"Saved configuration to {_config_path}")
            return True
        except Exception as e:
            logger.error(f"Error saving config to {_config_path}: {e}")
            return False

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
            f"<a href='/trust' class='navlink {'active' if active=='trust' else ''}'>TRUST</a>"
            f"<a href='/vstp' class='navlink {'active' if active=='vstp' else ''}'>VSTP</a>"
            f"<a href='/cif' class='navlink {'active' if active=='cif' else ''}'>CIF</a>"
            f"<a href='/tocs' class='navlink {'active' if active=='tocs' else ''}'>TOCs</a>"
            f"<a href='/toc-td-areas' class='navlink {'active' if active=='toc-td-areas' else ''}'>TOC-TD Areas</a>"
            f"<a href='/signal-mappings' class='navlink {'active' if active=='signal-mappings' else ''}'>Signal Mappings</a>"
            f"<a href='/stats' class='navlink {'active' if active=='stats' else ''}'>Stats</a>"
            "</div>"
            "<div class='quickfilter'>"
            f"<form method='get' action='/'><input name='area' placeholder='Area' value='{area}' size='4'/> "
            f"<input name='hc' placeholder='Headcode' value='{hc}' size='6'/> "
            "<button type='submit'>Filter</button></form>"
            "</div>"
            "<div class='config-icon'>"
            f"<a href='/config' class='navlink icon-link {'active' if active=='config' else ''}' title='Configuration'>⚙️</a>"
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
            ".quickfilter{margin-left:auto;margin-right:12px}"
            ".quickfilter input{padding:6px 8px;border-radius:6px;border:0;margin-right:6px}"
            ".quickfilter button{padding:6px 8px;border-radius:6px;border:0;background:#ffd24d;color:#222}"
            ".config-icon{display:flex;align-items:center}"
            ".icon-link{font-size:24px;padding:8px 12px}"
            "table{border-collapse:collapse;width:100%;background:white;margin-top:8px}"
            "th,td{border-bottom:1px solid #eee;padding:8px 10px;font-size:14px;text-align:left}"
            "th{background:#f7f9fc;font-weight:600;cursor:pointer;user-select:none}"
            "th a{display:block;width:100%;height:100%}"
            "th:hover{background:#e8f0fe}"
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
        # area pills
        body.append("<div>Filter: " + " ".join([f"<a class='pill' href='/?area={a}'>{a}</a>" for a in areas]) + " <a class='pill' href='/'>ALL</a></div>")
        body.append("<h3>Latest TD state" + (f" (area {area})" if area else "") + "</h3>")
        # Add search filter box
        body.append("<div style='margin:10px 0'><input type='text' id='tableFilter' placeholder='Filter by headcode, location, berth...' style='padding:8px;width:300px;border:1px solid #ccc;border-radius:4px'/> <span id='filterCount' style='margin-left:10px;color:#6c757d'></span></div>")
        body.append("<table id='tdStateTable'><thead><tr><th onclick='sortTable(0)' style='cursor:pointer'>Area <span id='sort0'></span></th><th onclick='sortTable(1)' style='cursor:pointer'>Headcode <span id='sort1'></span></th><th onclick='sortTable(2)' style='cursor:pointer'>Time <span id='sort2'></span></th><th onclick='sortTable(3)' style='cursor:pointer'>From <span id='sort3'></span></th><th onclick='sortTable(4)' style='cursor:pointer'>To <span id='sort4'></span></th><th onclick='sortTable(5)' style='cursor:pointer'>Location <span id='sort5'></span></th><th onclick='sortTable(6)' style='cursor:pointer'>Plat <span id='sort6'></span></th><th onclick='sortTable(7)' style='cursor:pointer'>Sched <span id='sort7'></span></th></tr></thead><tbody>")
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
        body.append("</tbody></table>")
        
        # Add JavaScript for sorting and filtering
        body.append("""
<script>
// Sorting functionality
let sortDirection = {};
function sortTable(columnIndex) {
    const table = document.getElementById('tdStateTable');
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    
    // Toggle sort direction
    sortDirection[columnIndex] = !sortDirection[columnIndex];
    const ascending = sortDirection[columnIndex];
    
    // Clear all sort indicators
    for (let i = 0; i < 8; i++) {
        const sortSpan = document.getElementById('sort' + i);
        if (sortSpan) sortSpan.textContent = '';
    }
    
    // Set current sort indicator
    const currentSortSpan = document.getElementById('sort' + columnIndex);
    if (currentSortSpan) currentSortSpan.textContent = ascending ? ' ▲' : ' ▼';
    
    // Sort rows
    rows.sort((a, b) => {
        let aVal = a.cells[columnIndex].textContent.trim();
        let bVal = b.cells[columnIndex].textContent.trim();
        
        // Handle timestamps (ISO format YYYY-MM-DD...)
        if (columnIndex === 2 && aVal && bVal) {
            return ascending ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
        }
        
        // Handle empty values
        if (!aVal) return ascending ? 1 : -1;
        if (!bVal) return ascending ? -1 : 1;
        
        // Default: case-insensitive string comparison
        const comparison = aVal.toLowerCase().localeCompare(bVal.toLowerCase());
        return ascending ? comparison : -comparison;
    });
    
    // Re-append sorted rows
    rows.forEach(row => tbody.appendChild(row));
}

// Filtering functionality
const filterInput = document.getElementById('tableFilter');
const filterCount = document.getElementById('filterCount');
const table = document.getElementById('tdStateTable');
const tbody = table.querySelector('tbody');
const allRows = Array.from(tbody.querySelectorAll('tr'));

function updateFilter() {
    const filterText = filterInput.value.toLowerCase();
    let visibleCount = 0;
    
    allRows.forEach(row => {
        const text = row.textContent.toLowerCase();
        if (text.includes(filterText)) {
            row.style.display = '';
            visibleCount++;
        } else {
            row.style.display = 'none';
        }
    });
    
    if (filterText) {
        filterCount.textContent = `Showing ${visibleCount} of ${allRows.length} rows`;
    } else {
        filterCount.textContent = '';
    }
}

filterInput.addEventListener('input', updateFilter);
</script>
""")
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
            body.append("<h3>Train State</h3>")
            body.append("<table>")
            body.append("<tr><th>Field</th><th>Value</th></tr>")
            body.append(f"<tr><td><b>TD Area</b></td><td>{r['td_area']}</td></tr>")
            body.append(f"<tr><td><b>Headcode</b></td><td>{r['headcode']}</td></tr>")
            
            # Format timestamp
            last_time = r['last_time_iso'] if r['last_time_iso'] else 'N/A'
            body.append(f"<tr><td><b>Last Time</b></td><td class='mono'>{last_time}</td></tr>")
            
            body.append(f"<tr><td><b>From Berth</b></td><td>{r['from_berth'] if r['from_berth'] else 'N/A'}</td></tr>")
            body.append(f"<tr><td><b>To Berth</b></td><td>{r['to_berth'] if r['to_berth'] else 'N/A'}</td></tr>")
            
            # Location with STANOX
            loc = r['location_name'] if 'location_name' in r.keys() and r['location_name'] else ''
            stanox = r['stanox'] if 'stanox' in r.keys() and r['stanox'] else ''
            location_display = f"{loc} ({stanox})" if stanox and loc else (loc if loc else stanox if stanox else 'N/A')
            body.append(f"<tr><td><b>Location</b></td><td>{location_display}</td></tr>")
            
            body.append(f"<tr><td><b>Platform</b></td><td>{r['platform'] if 'platform' in r.keys() and r['platform'] else 'N/A'}</td></tr>")
            
            # Schedule information
            sched_dep = r['sched_dep'] if 'sched_dep' in r.keys() and r['sched_dep'] else ''
            sched_arr = r['sched_arr'] if 'sched_arr' in r.keys() and r['sched_arr'] else ''
            origin = r['origin_name'] if 'origin_name' in r.keys() and r['origin_name'] else ''
            dest = r['dest_name'] if 'dest_name' in r.keys() and r['dest_name'] else ''
            
            if sched_dep or sched_arr:
                sched_times = f"{sched_dep if sched_dep else 'N/A'} → {sched_arr if sched_arr else 'N/A'}"
                body.append(f"<tr><td><b>Schedule Times</b></td><td class='mono'>{sched_times}</td></tr>")
            
            if origin or dest:
                sched_route = f"{origin if origin else 'N/A'} → {dest if dest else 'N/A'}"
                body.append(f"<tr><td><b>Schedule Route</b></td><td>{sched_route}</td></tr>")
            
            body.append("</table>")
        if ev:
            body.append("<h3>Recent berth events</h3><table><tr><th>Time</th><th>Type</th><th>From</th><th>To</th></tr>")
            for r in ev:
                body.append(f"<tr><td class='mono'>{r['ts_iso']}</td><td>{r['msg_type']}</td><td>{r['from_berth']}</td><td>{r['to_berth']}</td></tr>")
            body.append("</table>")
        return render_page(f"Train {hc}", body, active="home")

    @app.route("/config", methods=["GET", "POST"])
    def config():
        """Configuration interface for adjusting and saving YAML config, including mapper settings."""
        body = ["<h2>Configuration</h2>"]
        
        # Handle POST - save configuration
        if request.method == "POST":
            action = request.form.get("action", "")
            
            if action == "save_config":
                # Build config dict from form data
                config_data = load_yaml_config()
                
                # Authentication
                if request.form.get("user"):
                    config_data["user"] = request.form.get("user")
                if request.form.get("password"):
                    config_data["password"] = request.form.get("password")
                
                # STOMP Connection
                if request.form.get("host"):
                    config_data["host"] = request.form.get("host")
                if request.form.get("port"):
                    config_data["port"] = int(request.form.get("port", 61618))
                if request.form.get("vhost"):
                    config_data["vhost"] = request.form.get("vhost")
                
                # Filtering
                headcode = request.form.get("headcode", "").strip()
                config_data["headcode"] = headcode if headcode else ""
                uid = request.form.get("uid", "").strip()
                config_data["uid"] = uid if uid else ""
                td_area_str = request.form.get("td_area", "").strip()
                config_data["td_area"] = [a.strip() for a in td_area_str.split(",") if a.strip()] if td_area_str else []
                # TOC filter (multi-select list)
                toc_codes = request.form.getlist("toc_filter")
                config_data["toc_filter"] = [t.strip() for t in toc_codes if t.strip()] if toc_codes else []
                
                # Display Options
                config_data["width"] = int(request.form.get("width", 96))
                config_data["pretty"] = request.form.get("pretty") == "on"
                config_data["interactive"] = request.form.get("interactive") == "on"
                config_data["only_changes"] = request.form.get("only_changes") == "on"
                config_data["repeat_after"] = int(request.form.get("repeat_after", 300))
                
                # Logging
                config_data["log_level"] = request.form.get("log_level", "error")
                config_data["verbose"] = request.form.get("verbose") == "on"
                config_data["trace_headcode"] = request.form.get("trace_headcode") == "on"
                config_data["status_every"] = int(request.form.get("status_every", 15))
                
                # Reference Data Caching
                if request.form.get("corpus_cache"):
                    config_data["corpus_cache"] = request.form.get("corpus_cache")
                config_data["corpus_refresh"] = request.form.get("corpus_refresh") == "on"
                if request.form.get("smart_cache"):
                    config_data["smart_cache"] = request.form.get("smart_cache")
                config_data["smart_refresh"] = request.form.get("smart_refresh") == "on"
                if request.form.get("schedule_cache"):
                    config_data["schedule_cache"] = request.form.get("schedule_cache")
                config_data["schedule_refresh"] = request.form.get("schedule_refresh") == "on"
                config_data["use_schedule"] = request.form.get("use_schedule") == "on"
                if request.form.get("schedule_type"):
                    config_data["schedule_type"] = request.form.get("schedule_type")
                if request.form.get("schedule_day"):
                    config_data["schedule_day"] = request.form.get("schedule_day")
                
                # Database & Web
                if request.form.get("db_path"):
                    config_data["db_path"] = request.form.get("db_path")
                web_port_str = request.form.get("web_port", "").strip()
                config_data["web_port"] = int(web_port_str) if web_port_str else None
                config_data["enable_mapper"] = request.form.get("enable_mapper") == "on"
                config_data["save_raw_json"] = request.form.get("save_raw_json") == "on"
                
                # Data Retention
                retain_trust_days_str = request.form.get("retain_trust_days", "").strip()
                if retain_trust_days_str:
                    config_data["retain-trust-days"] = int(retain_trust_days_str)
                elif "retain-trust-days" in config_data:
                    del config_data["retain-trust-days"]
                
                retain_vstp_days_str = request.form.get("retain_vstp_days", "").strip()
                if retain_vstp_days_str:
                    config_data["retain-vstp-days"] = int(retain_vstp_days_str)
                elif "retain-vstp-days" in config_data:
                    del config_data["retain-vstp-days"]
                
                retention_interval_str = request.form.get("retention_interval", "").strip()
                if retention_interval_str:
                    config_data["retention-interval"] = int(retention_interval_str)
                elif "retention-interval" in config_data:
                    del config_data["retention-interval"]
                
                retention_batch_size_str = request.form.get("retention_batch_size", "").strip()
                if retention_batch_size_str:
                    config_data["retention-batch-size"] = int(retention_batch_size_str)
                elif "retention-batch-size" in config_data:
                    del config_data["retention-batch-size"]
                
                # Save to file
                if save_yaml_config(config_data):
                    body.append("<p style='color:green;padding:10px;background:#f0f9ff;border-radius:6px'><b>✓ Configuration saved successfully!</b></p>")
                else:
                    body.append("<p style='color:red;padding:10px;background:#fff0f0;border-radius:6px'><b>✗ Error saving configuration. Check logs for details.</b></p>")
            
            elif action == "rebuild_mapper":
                # Handle mapper rebuild (moved from /mapper route)
                pre_ms = int(request.form.get("pre_ms", 1000))
                post_ms = int(request.form.get("post_ms", 5000))
                tau_ms = int(request.form.get("tau_ms", 2500))
                td_area_filter = request.form.get("td_area_filter", "").strip()
                save_mapper_config = request.form.get("save_mapper_config") == "on"
                
                try:
                    # Save config if requested
                    if save_mapper_config:
                        cursor = conn.cursor()
                        cursor.execute("UPDATE mapper_config SET value=?, updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE key='pre_ms'", (pre_ms,))
                        cursor.execute("UPDATE mapper_config SET value=?, updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE key='post_ms'", (post_ms,))
                        cursor.execute("UPDATE mapper_config SET value=?, updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE key='tau_ms'", (tau_ms,))
                        conn.commit()
                        body.append("<p style='color:green'><b>Mapper configuration saved to database</b></p>")
                    
                    # Import mapper function
                    from .mapper import process_batch_for_mapper
                    from datetime import timezone
                    
                    # Delete existing scores
                    cursor = conn.cursor()
                    if td_area_filter:
                        cursor.execute("DELETE FROM berth_signal_scores WHERE td_area=?", (td_area_filter,))
                        deleted = cursor.rowcount
                    else:
                        cursor.execute("DELETE FROM berth_signal_scores")
                        deleted = cursor.rowcount
                    
                    # Fetch observations and rebuild
                    if td_area_filter:
                        obs_sql = "SELECT * FROM berth_signal_observations WHERE td_area=? ORDER BY step_timestamp"
                        obs = q(obs_sql, (td_area_filter,))
                    else:
                        obs_sql = "SELECT * FROM berth_signal_observations ORDER BY step_timestamp"
                        obs = q(obs_sql)
                    
                    total_observations = len(obs)
                    total_inserted = 0
                    progress_messages = [f"Processing {total_observations} observations with pre_ms={pre_ms}, post_ms={post_ms}, tau_ms={tau_ms}"]
                    
                    if obs:
                        # Group observations by TD area for batched processing
                        from collections import defaultdict
                        area_groups = defaultdict(list)
                        for row in obs:
                            td_area_val = row["td_area"]
                            area_groups[td_area_val].append(row)
                        
                        # Process each area
                        for td_area_val, area_obs in area_groups.items():
                            progress_messages.append(f"Processing area {td_area_val}: {len(area_obs)} observations")
                            
                            # Build event list from observations
                            events = []
                            for row in area_obs:
                                step_ts = row["step_timestamp"]
                                from_b = row["from_berth"]
                                to_b = row["to_berth"]
                                descr = row["descr"]
                                sig_ts = row["signal_timestamp"]
                                addr = row["address"]
                                data = row["data"]
                                
                                # Add step event
                                if step_ts and from_b and to_b:
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
                            
                            # Deduplicate events
                            seen = set()
                            unique_events = []
                            for e in events:
                                key = (e['msg_type'], e['msg_ts'], e.get('address'), e.get('from_berth'), e.get('to_berth'))
                                if key not in seen:
                                    seen.add(key)
                                    unique_events.append(e)
                            
                            if unique_events:
                                # Reprocess with new parameters
                                obs_rows, score_rows = process_batch_for_mapper(
                                    unique_events,
                                    pre_ms=pre_ms,
                                    post_ms=post_ms,
                                    tau_ms=tau_ms
                                )
                                
                                if score_rows:
                                    # Insert new scores
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
                    logger.error(f"Web dashboard: Error during mapper rebuild: {e}")
                    body.append(f"<p style='color:red'>Error during rebuild: {e}</p>")
                    import traceback
                    body.append(f"<pre style='font-size:11px;background:#f7f9fc;padding:8px'>{traceback.format_exc()}</pre>")
        
        # Load current configuration
        config_data = load_yaml_config()
        
        # Check if config file exists
        if not _config_path or not pathlib.Path(_config_path).exists():
            body.append(f"<p style='color:orange;padding:10px;background:#fff8e6;border-radius:6px'><b>⚠ No configuration file specified or file not found.</b></p>")
            if _config_path:
                body.append(f"<p class='dim'>Expected: {_config_path}</p>")
            body.append("<p>Configuration changes will not be saved to file. You can still adjust mapper settings below.</p>")
        
        # Get mapper config from database
        mapper_config = {"pre_ms": 1000, "post_ms": 5000, "tau_ms": 2500}
        mapper_stats = {"obs_count": 0, "score_count": 0, "areas": 0}
        try:
            mapper_cfg_rows = q("SELECT key, value FROM mapper_config")
            mapper_config = {row[0]: int(row[1]) for row in mapper_cfg_rows}
            
            obs_count = q("SELECT COUNT(*) as cnt FROM berth_signal_observations")[0]["cnt"]
            score_count = q("SELECT COUNT(*) as cnt FROM berth_signal_scores")[0]["cnt"]
            areas = q("SELECT COUNT(DISTINCT td_area) as cnt FROM berth_signal_observations")[0]["cnt"]
            mapper_stats = {"obs_count": obs_count, "score_count": score_count, "areas": areas}
        except Exception:
            pass
        
        # Render configuration form
        body.append("<h3>Application Configuration</h3>")
        body.append("<p class='dim'>Adjust settings below and click 'Save Configuration' to persist changes to the YAML file.</p>")
        
        body.append("<form method='post' style='max-width:800px'>")
        body.append("<input type='hidden' name='action' value='save_config'>")
        
        # Authentication Section
        body.append("<fieldset style='border:1px solid #ddd;padding:15px;margin:15px 0;border-radius:6px'>")
        body.append("<legend style='font-weight:600;padding:0 8px'>Network Rail Authentication</legend>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Email/Username:</label><input type='text' name='user' value=\"{config_data.get('user', '')}\" style='width:300px;padding:6px'></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Password:</label><input type='password' name='password' value=\"{config_data.get('password', '')}\" style='width:300px;padding:6px'></div>")
        body.append("</fieldset>")
        
        # STOMP Connection Section
        body.append("<fieldset style='border:1px solid #ddd;padding:15px;margin:15px 0;border-radius:6px'>")
        body.append("<legend style='font-weight:600;padding:0 8px'>STOMP Connection</legend>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Host:</label><input type='text' name='host' value=\"{config_data.get('host', 'publicdatafeeds.networkrail.co.uk')}\" style='width:300px;padding:6px'></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Port:</label><input type='number' name='port' value=\"{config_data.get('port', 61618)}\" style='width:100px;padding:6px'></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Virtual Host:</label><input type='text' name='vhost' value=\"{config_data.get('vhost', 'publicdatafeeds.networkrail.co.uk')}\" style='width:300px;padding:6px'></div>")
        body.append("</fieldset>")
        
        # Filtering Section
        body.append("<fieldset style='border:1px solid #ddd;padding:15px;margin:15px 0;border-radius:6px'>")
        body.append("<legend style='font-weight:600;padding:0 8px'>Filtering Options</legend>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Headcode Filter:</label><input type='text' name='headcode' value=\"{config_data.get('headcode', '')}\" placeholder='e.g., 2C90' style='width:150px;padding:6px'></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>UID Filter:</label><input type='text' name='uid' value=\"{config_data.get('uid', '')}\" placeholder='e.g., C43876' style='width:150px;padding:6px'></div>")
        td_area_val = ','.join(config_data.get('td_area', [])) if isinstance(config_data.get('td_area'), list) else ''
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>TD Area Filter:</label><input type='text' name='td_area' value=\"{td_area_val}\" placeholder='e.g., EK,WR' style='width:200px;padding:6px'><span class='dim' style='margin-left:8px'>Comma-separated</span></div>")
        
        # TOC Filter (multi-select)
        body.append("<div style='margin-bottom:10px'>")
        body.append("<label style='display:inline-block;width:180px;vertical-align:top'>TOC Filter:</label>")
        body.append("<div style='display:inline-block;max-height:200px;overflow-y:auto;border:1px solid #ccc;padding:8px;border-radius:4px;background:white'>")
        
        # Get TOC codes from database
        try:
            toc_rows = q("SELECT toc_code, toc_name FROM toc_reference ORDER BY toc_code")
            selected_tocs = config_data.get('toc_filter', [])
            if not isinstance(selected_tocs, list):
                selected_tocs = []
            
            if toc_rows:
                for toc_row in toc_rows:
                    code = toc_row['toc_code']
                    name = toc_row['toc_name']
                    checked = 'checked' if code in selected_tocs else ''
                    body.append(f"<div style='margin:2px 0'><label style='display:block'><input type='checkbox' name='toc_filter' value='{code}' {checked}> <span class='mono' style='font-weight:600'>{code}</span> - {name}</label></div>")
            else:
                body.append("<p class='dim' style='margin:0'>No TOC data available</p>")
        except Exception as e:
            logger.error(f"Failed to load TOC list for config: {e}")
            body.append(f"<p class='dim' style='margin:0'>Error loading TOC list: {e}</p>")
        
        body.append("</div>")
        body.append("<br><span class='dim' style='margin-left:188px'>Select TOCs to filter TRUST messages (leave unchecked for all)</span>")
        body.append("</div>")
        
        body.append("</fieldset>")
        
        # Display Options Section
        body.append("<fieldset style='border:1px solid #ddd;padding:15px;margin:15px 0;border-radius:6px'>")
        body.append("<legend style='font-weight:600;padding:0 8px'>Display Options</legend>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Console Width:</label><input type='number' name='width' value=\"{config_data.get('width', 96)}\" style='width:100px;padding:6px'></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Pretty Output:</label><input type='checkbox' name='pretty' {'checked' if config_data.get('pretty', True) else ''}></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Interactive Mode:</label><input type='checkbox' name='interactive' {'checked' if config_data.get('interactive', False) else ''}></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Only Show Changes:</label><input type='checkbox' name='only_changes' {'checked' if config_data.get('only_changes', True) else ''}></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Repeat After (sec):</label><input type='number' name='repeat_after' value=\"{config_data.get('repeat_after', 300)}\" style='width:100px;padding:6px'></div>")
        body.append("</fieldset>")
        
        # Logging Section
        body.append("<fieldset style='border:1px solid #ddd;padding:15px;margin:15px 0;border-radius:6px'>")
        body.append("<legend style='font-weight:600;padding:0 8px'>Logging & Debugging</legend>")
        log_level = config_data.get('log_level', 'error')
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Log Level:</label><select name='log_level' style='padding:6px'>")
        for level in ['error', 'warning', 'info', 'verbose']:
            selected = 'selected' if level == log_level else ''
            body.append(f"<option value='{level}' {selected}>{level.title()}</option>")
        body.append("</select></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Verbose Mode:</label><input type='checkbox' name='verbose' {'checked' if config_data.get('verbose', False) else ''}></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Trace Headcode:</label><input type='checkbox' name='trace_headcode' {'checked' if config_data.get('trace_headcode', False) else ''}></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Status Every (sec):</label><input type='number' name='status_every' value=\"{config_data.get('status_every', 15)}\" style='width:100px;padding:6px'></div>")
        body.append("</fieldset>")
        
        # Reference Data Section
        body.append("<fieldset style='border:1px solid #ddd;padding:15px;margin:15px 0;border-radius:6px'>")
        body.append("<legend style='font-weight:600;padding:0 8px'>Reference Data Caching</legend>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>CORPUS Cache:</label><input type='text' name='corpus_cache' value=\"{config_data.get('corpus_cache', '~/.cache/openraildata/CORPUSExtract.json')}\" style='width:400px;padding:6px'></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>CORPUS Refresh:</label><input type='checkbox' name='corpus_refresh' {'checked' if config_data.get('corpus_refresh', False) else ''}></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>SMART Cache:</label><input type='text' name='smart_cache' value=\"{config_data.get('smart_cache', '~/.cache/openraildata/SMART.json')}\" style='width:400px;padding:6px'></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>SMART Refresh:</label><input type='checkbox' name='smart_refresh' {'checked' if config_data.get('smart_refresh', False) else ''}></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Schedule Cache:</label><input type='text' name='schedule_cache' value=\"{config_data.get('schedule_cache', '~/.cache/openraildata/SCHEDULE_toc-full.json.gz')}\" style='width:400px;padding:6px'></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Schedule Refresh:</label><input type='checkbox' name='schedule_refresh' {'checked' if config_data.get('schedule_refresh', False) else ''}></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Use Schedule:</label><input type='checkbox' name='use_schedule' {'checked' if config_data.get('use_schedule', True) else ''}></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Schedule Type:</label><input type='text' name='schedule_type' value=\"{config_data.get('schedule_type', 'CIF_ALL_FULL_DAILY')}\" style='width:250px;padding:6px'></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Schedule Day:</label><input type='text' name='schedule_day' value=\"{config_data.get('schedule_day', 'toc-full')}\" style='width:250px;padding:6px'></div>")
        body.append("</fieldset>")
        
        # Database & Web Section
        body.append("<fieldset style='border:1px solid #ddd;padding:15px;margin:15px 0;border-radius:6px'>")
        body.append("<legend style='font-weight:600;padding:0 8px'>Database & Web Dashboard</legend>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Database Path:</label><input type='text' name='db_path' value=\"{config_data.get('db_path', '~/.cache/openraildata/railhub.db')}\" style='width:400px;padding:6px'></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Web Port:</label><input type='number' name='web_port' value=\"{config_data.get('web_port', 8088) or ''}\" style='width:100px;padding:6px'><span class='dim' style='margin-left:8px'>Leave empty to disable</span></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Enable Mapper:</label><input type='checkbox' name='enable_mapper' {'checked' if config_data.get('enable_mapper', True) else ''}></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Save Raw JSON:</label><input type='checkbox' name='save_raw_json' {'checked' if config_data.get('save_raw_json', True) else ''}><span class='dim' style='margin-left:8px'>Disabling reduces database size</span></div>")
        body.append("</fieldset>")
        
        # Data Retention Section
        body.append("<fieldset style='border:1px solid #ddd;padding:15px;margin:15px 0;border-radius:6px'>")
        body.append("<legend style='font-weight:600;padding:0 8px'>Data Retention</legend>")
        body.append("<p class='dim' style='margin-top:0'>Configure automatic data retention. Changes require application restart.</p>")
        retain_trust_days = config_data.get('retain_trust_days') if config_data.get('retain_trust_days') is not None else config_data.get('retain-trust-days', '')
        retain_vstp_days = config_data.get('retain_vstp_days') if config_data.get('retain_vstp_days') is not None else config_data.get('retain-vstp-days', '')
        retention_interval = config_data.get('retention_interval') if config_data.get('retention_interval') is not None else config_data.get('retention-interval', 3600)
        retention_batch_size = config_data.get('retention_batch_size') if config_data.get('retention_batch_size') is not None else config_data.get('retention-batch-size', 1000)
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Retain TRUST (days):</label><input type='number' name='retain_trust_days' value=\"{retain_trust_days}\" placeholder='Leave empty to disable' style='width:150px;padding:6px'><span class='dim' style='margin-left:8px'>Days to keep TRUST messages</span></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Retain VSTP (days):</label><input type='number' name='retain_vstp_days' value=\"{retain_vstp_days}\" placeholder='Leave empty to disable' style='width:150px;padding:6px'><span class='dim' style='margin-left:8px'>Days to keep VSTP schedules</span></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Check Interval (sec):</label><input type='number' name='retention_interval' value=\"{retention_interval}\" style='width:150px;padding:6px'><span class='dim' style='margin-left:8px'>Time between retention checks</span></div>")
        body.append(f"<div style='margin-bottom:10px'><label style='display:inline-block;width:180px'>Batch Size:</label><input type='number' name='retention_batch_size' value=\"{retention_batch_size}\" style='width:150px;padding:6px'><span class='dim' style='margin-left:8px'>Records per deletion batch</span></div>")
        body.append("</fieldset>")
        
        # Save button
        body.append("<div style='margin-top:20px'><button type='submit' style='padding:10px 20px;background:#0b5cff;color:white;border:0;border-radius:6px;font-weight:600;font-size:16px;cursor:pointer'>Save Configuration</button></div>")
        body.append("</form>")
        
        # Mapper Configuration & Rebuild Section
        body.append("<hr style='margin:40px 0;border:0;border-top:2px solid #eee'>")
        body.append("<h3>Mapper Configuration & Rebuild</h3>")
        body.append(f"<p><b>Observations:</b> {mapper_stats['obs_count']} | <b>Score Entries:</b> {mapper_stats['score_count']} | <b>TD Areas:</b> {mapper_stats['areas']}</p>")
        
        body.append("""
            <p class='dim' style='margin-bottom:15px'>
            Adjust the mapper parameters below and rebuild the berth-signal correlation scores.
            The rebuild will reprocess all existing observations with the new parameters.
            </p>
        """)
        
        body.append("<form method='post' style='background:#f7f9fc;padding:15px;border-radius:6px;max-width:800px'>")
        body.append("<input type='hidden' name='action' value='rebuild_mapper'>")
        body.append(f"<div style='margin-bottom:12px'><label style='display:inline-block;width:150px;font-weight:600'>pre_ms:</label><input type='number' name='pre_ms' value='{mapper_config.get('pre_ms', 1000)}' min='0' max='60000' required style='padding:6px;width:100px'><span class='dim' style='margin-left:8px'>Time window before step (ms)</span></div>")
        body.append(f"<div style='margin-bottom:12px'><label style='display:inline-block;width:150px;font-weight:600'>post_ms:</label><input type='number' name='post_ms' value='{mapper_config.get('post_ms', 5000)}' min='0' max='60000' required style='padding:6px;width:100px'><span class='dim' style='margin-left:8px'>Time window after step (ms)</span></div>")
        body.append(f"<div style='margin-bottom:12px'><label style='display:inline-block;width:150px;font-weight:600'>tau_ms:</label><input type='number' name='tau_ms' value='{mapper_config.get('tau_ms', 2500)}' min='1' max='60000' required style='padding:6px;width:100px'><span class='dim' style='margin-left:8px'>Exponential weight decay (ms)</span></div>")
        body.append("<div style='margin-bottom:12px'><label style='display:inline-block;width:150px;font-weight:600'>TD Area Filter:</label><input type='text' name='td_area_filter' value='' placeholder='(all areas)' style='padding:6px;width:100px'><span class='dim' style='margin-left:8px'>Optional: rebuild only this area</span></div>")
        body.append("<div style='margin-bottom:12px'><label style='display:inline-block;width:150px;font-weight:600'>Save Config:</label><input type='checkbox' name='save_mapper_config' value='yes' checked><span class='dim' style='margin-left:8px'>Save these parameters as defaults</span></div>")
        body.append("<div style='margin-top:16px'><button type='submit' style='padding:8px 16px;background:#0b5cff;color:white;border:0;border-radius:6px;font-weight:600;cursor:pointer'>Rebuild Mapper Scores</button></div>")
        body.append("</form>")
        
        body.append("""
            <h4 style='margin-top:25px'>Parameter Explanations</h4>
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
        
        return render_page("Configuration - NR RailHub", body, active="config")


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

    @app.get("/trust")
    def trust():
        """Display current TRUST state and recent TRUST messages."""
        # Get filter parameters
        train_id = request.args.get("train_id", "").strip()
        headcode = request.args.get("headcode", "").strip()
        view = request.args.get("view", "state").strip()  # 'state' or 'messages'
        
        # Additional filter parameters
        event_type = request.args.get("event_type", "").strip()
        stanox = request.args.get("stanox", "").strip()
        platform = request.args.get("platform", "").strip()
        variation_status = request.args.get("variation_status", "").strip()
        location = request.args.get("location", "").strip()
        
        # Get sort parameters
        sort_by = request.args.get("sort", "time").strip()
        sort_order = request.args.get("order", "desc").strip().lower()
        if sort_order not in ["asc", "desc"]:
            sort_order = "desc"
        
        # Load TOC filter from config
        config_data = load_yaml_config()
        toc_filter = config_data.get('toc_filter', [])
        if not isinstance(toc_filter, list):
            toc_filter = []
        
        body = []
        
        if view == "messages":
            # Show trust_messages history
            body.append("<h2>TRUST Messages History</h2>")
            body.append("<p><a href='/trust?view=state'>Switch to Current State View</a></p>")
            
            sql = """SELECT tm.id, tm.train_id, tm.actual_timestamp_ms, tm.event_type, tm.reporting_stanox, 
                     tm.toc_id AS msg_toc_id, tm.toc_code AS canonical_toc_code, tr.toc_name, 
                     tm.timetable_variation, tm.variation_status, tm.platform, tm.created_at_utc 
                     FROM trust_messages tm 
                     LEFT JOIN toc_reference tr ON tm.toc_code = tr.toc_code 
                     WHERE 1=1"""
            params = []
            
            if train_id:
                sql += " AND tm.train_id=?"
                params.append(train_id)
            if headcode:
                # Note: trust_messages doesn't have a separate headcode column,
                # but train_id often contains the headcode, so we search within it
                sql += " AND tm.train_id LIKE ?"
                params.append(f"%{headcode}%")
            if event_type:
                sql += " AND tm.event_type=?"
                params.append(event_type)
            if stanox:
                sql += " AND tm.reporting_stanox=?"
                params.append(stanox)
            if platform:
                sql += " AND tm.platform=?"
                params.append(platform)
            if variation_status:
                sql += " AND tm.variation_status=?"
                params.append(variation_status)
            
            # Apply TOC filter if configured (filter on canonical toc_code)
            if toc_filter:
                placeholders = ','.join('?' * len(toc_filter))
                sql += f" AND tm.toc_code IN ({placeholders})"
                params.extend(toc_filter)
            
            # Map sort column names to database columns
            sort_columns = {
                "id": "tm.id",
                "train_id": "tm.train_id",
                "time": "tm.actual_timestamp_ms",
                "event_type": "tm.event_type",
                "stanox": "tm.reporting_stanox",
                "toc": "tr.toc_name",
                "variation": "tm.timetable_variation",
                "status": "tm.variation_status",
                "platform": "tm.platform",
            }
            
            # Validate and apply sorting
            if sort_by in sort_columns:
                order_clause = f" ORDER BY {sort_columns[sort_by]} {sort_order.upper()}"
            else:
                order_clause = " ORDER BY tm.actual_timestamp_ms DESC"
            
            sql += order_clause + " LIMIT 500"
            
            try:
                rows = q(sql, params)
                
                # Helper function to build sort URL with current filters
                def sort_url(column: str) -> str:
                    """Build URL for column sorting while preserving filters."""
                    params = {"view": "messages"}
                    if train_id:
                        params['train_id'] = train_id
                    if headcode:
                        params['headcode'] = headcode
                    if event_type:
                        params['event_type'] = event_type
                    if stanox:
                        params['stanox'] = stanox
                    if platform:
                        params['platform'] = platform
                    if variation_status:
                        params['variation_status'] = variation_status
                    params['sort'] = column
                    # Toggle order if already sorting by this column
                    if sort_by == column:
                        params['order'] = 'asc' if sort_order == 'desc' else 'desc'
                    else:
                        # Default to desc for time/id, asc for others
                        params['order'] = 'desc' if column in ['time', 'id', 'variation'] else 'asc'
                    return f"/trust?{urlencode(params)}"
                
                def sort_indicator(column: str) -> str:
                    """Return sort indicator (arrow) if this column is currently sorted."""
                    if sort_by == column:
                        return " ▼" if sort_order == 'desc' else " ▲"
                    return ""
                
                # Filter form
                body.append("<h3>Filters</h3>")
                body.append("""
                    <form method='get' style='background:#f7f9fc;padding:15px;border-radius:6px;margin-bottom:16px'>
                        <input type='hidden' name='view' value='messages'>
                        <div style='display:grid;grid-template-columns:repeat(4,1fr);gap:12px'>
                            <div>
                                <label style='font-weight:600;display:block;margin-bottom:4px'>Train ID:</label>
                                <input type='text' name='train_id' value='""" + html.escape(train_id) + """' placeholder='e.g. 123A45678' style='padding:6px;width:100%'>
                            </div>
                            <div>
                                <label style='font-weight:600;display:block;margin-bottom:4px'>Headcode:</label>
                                <input type='text' name='headcode' value='""" + html.escape(headcode) + """' placeholder='e.g. 2C90' style='padding:6px;width:100%'>
                            </div>
                            <div>
                                <label style='font-weight:600;display:block;margin-bottom:4px'>Event Type:</label>
                                <input type='text' name='event_type' value='""" + html.escape(event_type) + """' placeholder='e.g. ARRIVAL' style='padding:6px;width:100%'>
                            </div>
                            <div>
                                <label style='font-weight:600;display:block;margin-bottom:4px'>STANOX:</label>
                                <input type='text' name='stanox' value='""" + html.escape(stanox) + """' placeholder='e.g. 87701' style='padding:6px;width:100%'>
                            </div>
                            <div>
                                <label style='font-weight:600;display:block;margin-bottom:4px'>Platform:</label>
                                <input type='text' name='platform' value='""" + html.escape(platform) + """' placeholder='e.g. 2' style='padding:6px;width:100%'>
                            </div>
                            <div>
                                <label style='font-weight:600;display:block;margin-bottom:4px'>Variation Status:</label>
                                <input type='text' name='variation_status' value='""" + html.escape(variation_status) + """' placeholder='e.g. LATE' style='padding:6px;width:100%'>
                            </div>
                        </div>
                        <div style='margin-top:12px'>
                            <button type='submit' style='padding:8px 16px;background:#0b5cff;color:white;border:0;border-radius:6px;font-weight:600;cursor:pointer;margin-right:8px'>Apply Filters</button>
                            <a href='/trust?view=messages' style='padding:8px 16px;background:#eee;color:#222;text-decoration:none;border-radius:6px;font-weight:600'>Clear Filters</a>
                        </div>
                    </form>
                """)
                
                if rows:
                    body.append(f"<p class='dim'>Showing {len(rows)} message(s)</p>")
                    body.append("<table>")
                    body.append("<tr>")
                    body.append(f"<th><a href='{sort_url('id')}' style='color:inherit;text-decoration:none'>ID{sort_indicator('id')}</a></th>")
                    body.append(f"<th><a href='{sort_url('train_id')}' style='color:inherit;text-decoration:none'>Train ID{sort_indicator('train_id')}</a></th>")
                    body.append(f"<th><a href='{sort_url('time')}' style='color:inherit;text-decoration:none'>Timestamp{sort_indicator('time')}</a></th>")
                    body.append(f"<th><a href='{sort_url('event_type')}' style='color:inherit;text-decoration:none'>Event Type{sort_indicator('event_type')}</a></th>")
                    body.append(f"<th><a href='{sort_url('stanox')}' style='color:inherit;text-decoration:none'>STANOX{sort_indicator('stanox')}</a></th>")
                    body.append(f"<th><a href='{sort_url('toc')}' style='color:inherit;text-decoration:none'>TOC{sort_indicator('toc')}</a></th>")
                    body.append(f"<th><a href='{sort_url('variation')}' style='color:inherit;text-decoration:none'>Variation (min){sort_indicator('variation')}</a></th>")
                    body.append(f"<th><a href='{sort_url('status')}' style='color:inherit;text-decoration:none'>Status{sort_indicator('status')}</a></th>")
                    body.append(f"<th><a href='{sort_url('platform')}' style='color:inherit;text-decoration:none'>Platform{sort_indicator('platform')}</a></th>")
                    body.append("<th>Created</th>")
                    body.append("</tr>")
                    for r in rows:
                        ts = datetime.fromtimestamp(r['actual_timestamp_ms'] / 1000.0).strftime('%Y-%m-%d %H:%M:%S') if r['actual_timestamp_ms'] else ''
                        variation = r['timetable_variation'] if r['timetable_variation'] is not None else ''
                        # Display TOC name (from canonical join), fallback to canonical code, then raw message code
                        toc_display_text = r['toc_name'] if r['toc_name'] else (r['canonical_toc_code'] or r['msg_toc_id'] or '')
                        toc_tooltip = r['msg_toc_id'] or ''
                        body.append(
                            f"<tr>"
                            f"<td>{r['id']}</td>"
                            f"<td class='mono'>{r['train_id']}</td>"
                            f"<td class='mono'>{ts}</td>"
                            f"<td>{r['event_type'] or ''}</td>"
                            f"<td>{r['reporting_stanox'] or ''}</td>"
                            f"<td title='Raw: {toc_tooltip}'>{toc_display_text}</td>"
                            f"<td>{variation}</td>"
                            f"<td>{r['variation_status'] or ''}</td>"
                            f"<td>{r['platform'] or ''}</td>"
                            f"<td class='mono dim'>{r['created_at_utc'][:19] if r['created_at_utc'] else ''}</td>"
                            f"</tr>"
                        )
                    body.append("</table>")
                else:
                    body.append("<p><i>No TRUST messages found</i></p>")
            except Exception as e:
                logger.error(f"Web dashboard: Error querying trust_messages: {e}")
                body.append(f"<p><i>Error querying trust_messages: {e}</i></p>")
        else:
            # Show trust_state (current state)
            body.append("<h2>TRUST Current State</h2>")
            body.append("<p><a href='/trust?view=messages'>Switch to Messages History View</a></p>")
            
            sql = """SELECT ts.train_id, ts.headcode, ts.uid, ts.toc_id, tr.toc_name, 
                     ts.last_event_time, ts.last_location, ts.last_delay_min 
                     FROM trust_state ts 
                     LEFT JOIN toc_reference tr ON ts.toc_id = tr.toc_code 
                     WHERE 1=1"""
            params = []
            
            if train_id:
                sql += " AND ts.train_id=?"
                params.append(train_id)
            if headcode:
                sql += " AND ts.headcode=?"
                params.append(headcode)
            if location:
                sql += " AND ts.last_location LIKE ?"
                params.append(f"%{location}%")
            
            # Apply TOC filter if configured
            if toc_filter:
                placeholders = ','.join('?' * len(toc_filter))
                sql += f" AND ts.toc_id IN ({placeholders})"
                params.extend(toc_filter)
            
            # Map sort column names to database columns
            sort_columns = {
                "train_id": "ts.train_id",
                "headcode": "ts.headcode",
                "uid": "ts.uid",
                "toc": "tr.toc_name",
                "time": "ts.last_event_time",
                "location": "ts.last_location",
                "delay": "ts.last_delay_min",
            }
            
            # Validate and apply sorting
            if sort_by in sort_columns:
                order_clause = f" ORDER BY {sort_columns[sort_by]} {sort_order.upper()}"
            else:
                order_clause = " ORDER BY ts.last_event_time DESC"
            
            sql += order_clause + " LIMIT 500"
            
            try:
                rows = q(sql, params)
                
                # Helper function to build sort URL with current filters
                def sort_url(column: str) -> str:
                    """Build URL for column sorting while preserving filters."""
                    params = {"view": "state"}
                    if train_id:
                        params['train_id'] = train_id
                    if headcode:
                        params['headcode'] = headcode
                    if location:
                        params['location'] = location
                    params['sort'] = column
                    # Toggle order if already sorting by this column
                    if sort_by == column:
                        params['order'] = 'asc' if sort_order == 'desc' else 'desc'
                    else:
                        # Default to desc for time/delay, asc for others
                        params['order'] = 'desc' if column in ['time', 'delay'] else 'asc'
                    return f"/trust?{urlencode(params)}"
                
                def sort_indicator(column: str) -> str:
                    """Return sort indicator (arrow) if this column is currently sorted."""
                    if sort_by == column:
                        return " ▼" if sort_order == 'desc' else " ▲"
                    return ""
                
                # Filter form
                body.append("<h3>Filters</h3>")
                body.append("""
                    <form method='get' style='background:#f7f9fc;padding:15px;border-radius:6px;margin-bottom:16px'>
                        <input type='hidden' name='view' value='state'>
                        <div style='display:grid;grid-template-columns:repeat(3,1fr);gap:12px'>
                            <div>
                                <label style='font-weight:600;display:block;margin-bottom:4px'>Train ID:</label>
                                <input type='text' name='train_id' value='""" + html.escape(train_id) + """' placeholder='e.g. 123A45678' style='padding:6px;width:100%'>
                            </div>
                            <div>
                                <label style='font-weight:600;display:block;margin-bottom:4px'>Headcode:</label>
                                <input type='text' name='headcode' value='""" + html.escape(headcode) + """' placeholder='e.g. 2C90' style='padding:6px;width:100%'>
                            </div>
                            <div>
                                <label style='font-weight:600;display:block;margin-bottom:4px'>Location:</label>
                                <input type='text' name='location' value='""" + html.escape(location) + """' placeholder='e.g. Clapham' style='padding:6px;width:100%'>
                            </div>
                        </div>
                        <div style='margin-top:12px'>
                            <button type='submit' style='padding:8px 16px;background:#0b5cff;color:white;border:0;border-radius:6px;font-weight:600;cursor:pointer;margin-right:8px'>Apply Filters</button>
                            <a href='/trust?view=state' style='padding:8px 16px;background:#eee;color:#222;text-decoration:none;border-radius:6px;font-weight:600'>Clear Filters</a>
                        </div>
                    </form>
                """)
                
                if rows:
                    body.append(f"<p class='dim'>Showing {len(rows)} train(s)</p>")
                    body.append("<table>")
                    body.append("<tr>")
                    body.append(f"<th><a href='{sort_url('train_id')}' style='color:inherit;text-decoration:none'>Train ID{sort_indicator('train_id')}</a></th>")
                    body.append(f"<th><a href='{sort_url('headcode')}' style='color:inherit;text-decoration:none'>Headcode{sort_indicator('headcode')}</a></th>")
                    body.append(f"<th><a href='{sort_url('uid')}' style='color:inherit;text-decoration:none'>UID{sort_indicator('uid')}</a></th>")
                    body.append(f"<th><a href='{sort_url('toc')}' style='color:inherit;text-decoration:none'>TOC{sort_indicator('toc')}</a></th>")
                    body.append(f"<th><a href='{sort_url('time')}' style='color:inherit;text-decoration:none'>Last Event Time{sort_indicator('time')}</a></th>")
                    body.append(f"<th><a href='{sort_url('location')}' style='color:inherit;text-decoration:none'>Last Location{sort_indicator('location')}</a></th>")
                    body.append(f"<th><a href='{sort_url('delay')}' style='color:inherit;text-decoration:none'>Delay (min){sort_indicator('delay')}</a></th>")
                    body.append("<th>Actions</th>")
                    body.append("</tr>")
                    for r in rows:
                        delay_display = f"{r['last_delay_min']}" if r['last_delay_min'] is not None else 'N/A'
                        # Display TOC name with code in tooltip
                        toc_display = r['toc_name'] if r['toc_name'] else (r['toc_id'] or '')
                        toc_code = r['toc_id'] or ''
                        body.append(
                            f"<tr>"
                            f"<td class='mono'>{r['train_id']}</td>"
                            f"<td><b>{r['headcode'] or ''}</b></td>"
                            f"<td class='mono'>{r['uid'] or ''}</td>"
                            f"<td title='{toc_code}'>{toc_display}</td>"
                            f"<td class='mono'>{r['last_event_time'] or ''}</td>"
                            f"<td>{r['last_location'] or ''}</td>"
                            f"<td>{delay_display}</td>"
                            f"<td><a href='/trust?view=messages&train_id={r['train_id']}'>View Messages</a></td>"
                            f"</tr>"
                        )
                    body.append("</table>")
                else:
                    body.append("<p><i>No TRUST state data found</i></p>")
            except Exception as e:
                logger.error(f"Web dashboard: Error querying trust_state: {e}")
                body.append(f"<p><i>Error querying trust_state: {e}</i></p>")
        
        return render_page("TRUST - NR RailHub", body, active="trust")

    @app.get("/vstp")
    def vstp():
        """Display VSTP schedules with filtering, sorting, and pagination."""
        # Get filter parameters
        uid = request.args.get("uid", "").strip()
        headcode = request.args.get("headcode", "").strip()
        status = request.args.get("status", "").strip()
        category = request.args.get("category", "").strip()
        start_date_from = request.args.get("start_date_from", "").strip()
        start_date_to = request.args.get("start_date_to", "").strip()
        
        # Get sorting parameters
        sort_by = request.args.get("sort_by", "created_at_utc").strip()
        sort_dir = request.args.get("sort_dir", "DESC").strip().upper()
        if sort_dir not in ["ASC", "DESC"]:
            sort_dir = "DESC"
        
        # Validate sort_by to prevent SQL injection
        valid_sort_columns = ["uid", "schedule_start_date", "schedule_end_date", "transaction_type", 
                              "train_status", "created_at_utc", "CIF_train_category"]
        if sort_by not in valid_sort_columns:
            sort_by = "created_at_utc"
        
        # Pagination
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            page = 1
        per_page = 50
        offset = (page - 1) * per_page
        
        view = request.args.get("view", "schedules").strip()  # 'schedules' or 'state'
        
        body = []
        
        if view == "state":
            # Show vstp_state (simplified view)
            body.append("<h2>VSTP State Summary</h2>")
            body.append("<p><a href='/vstp?view=schedules'>Switch to Detailed Schedules View</a></p>")
            
            sql = "SELECT uid, headcode, start_date, end_date FROM vstp_state WHERE 1=1"
            params = []
            
            if uid:
                sql += " AND uid=?"
                params.append(uid)
            if headcode:
                sql += " AND headcode=?"
                params.append(headcode)
            
            sql += " ORDER BY start_date DESC LIMIT 500"
            
            try:
                rows = q(sql, params)
                if rows:
                    body.append(f"<p class='dim'>Showing {len(rows)} schedule(s)</p>")
                    body.append("<table>")
                    body.append("<tr><th>UID</th><th>Headcode</th><th>Start Date</th><th>End Date</th><th>Actions</th></tr>")
                    for r in rows:
                        body.append(
                            f"<tr>"
                            f"<td class='mono'>{r['uid']}</td>"
                            f"<td><b>{r['headcode'] or ''}</b></td>"
                            f"<td class='mono'>{r['start_date']}</td>"
                            f"<td class='mono'>{r['end_date'] or ''}</td>"
                            f"<td><a href='/vstp?view=schedules&uid={r['uid']}'>View Details</a></td>"
                            f"</tr>"
                        )
                    body.append("</table>")
                else:
                    body.append("<p><i>No VSTP state data found</i></p>")
            except Exception as e:
                logger.error(f"Web dashboard: Error querying vstp_state: {e}")
                body.append(f"<p><i>Error querying vstp_state: {e}</i></p>")
        else:
            # Show vstp_schedules with locations
            body.append("<h2>VSTP Schedules</h2>")
            body.append("<p><a href='/vstp?view=state'>Switch to State Summary View</a></p>")
            
            # Add filter form
            body.append("<div style='background:#f7f9fc;padding:15px;border-radius:8px;margin:15px 0'>")
            body.append("<form method='get' action='/vstp' style='display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px'>")
            body.append(f"<input type='hidden' name='view' value='schedules'/>")
            body.append(f"<div><label style='font-size:12px;color:#666'>UID:</label><br/><input type='text' name='uid' value='{html.escape(uid)}' placeholder='Schedule UID' style='width:100%;padding:6px;border:1px solid #ddd;border-radius:4px'/></div>")
            body.append(f"<div><label style='font-size:12px;color:#666'>Headcode:</label><br/><input type='text' name='headcode' value='{html.escape(headcode)}' placeholder='Train headcode' style='width:100%;padding:6px;border:1px solid #ddd;border-radius:4px'/></div>")
            body.append(f"<div><label style='font-size:12px;color:#666'>Status:</label><br/><input type='text' name='status' value='{html.escape(status)}' placeholder='Train status' style='width:100%;padding:6px;border:1px solid #ddd;border-radius:4px'/></div>")
            body.append(f"<div><label style='font-size:12px;color:#666'>Category:</label><br/><input type='text' name='category' value='{html.escape(category)}' placeholder='Category' style='width:100%;padding:6px;border:1px solid #ddd;border-radius:4px'/></div>")
            body.append(f"<div><label style='font-size:12px;color:#666'>Start Date From:</label><br/><input type='date' name='start_date_from' value='{html.escape(start_date_from)}' style='width:100%;padding:6px;border:1px solid #ddd;border-radius:4px'/></div>")
            body.append(f"<div><label style='font-size:12px;color:#666'>Start Date To:</label><br/><input type='date' name='start_date_to' value='{html.escape(start_date_to)}' style='width:100%;padding:6px;border:1px solid #ddd;border-radius:4px'/></div>")
            body.append("<div style='grid-column:span 2'><button type='submit' style='padding:8px 16px;background:#0b5cff;color:white;border:0;border-radius:4px;cursor:pointer;margin-right:8px'>Apply Filters</button>")
            body.append("<a href='/vstp?view=schedules' style='padding:8px 16px;background:#eee;color:#333;border:0;border-radius:4px;text-decoration:none;display:inline-block'>Clear Filters</a></div>")
            body.append("</form></div>")
            
            sql = """SELECT uid, schedule_start_date, schedule_end_date, transaction_type, 
                     train_status, signalling_id, CIF_train_service_code, CIF_train_category, 
                     CIF_power_type, sender_organisation, created_at_utc 
                     FROM vstp_schedules WHERE 1=1"""
            params = []
            
            if uid:
                sql += " AND uid=?"
                params.append(uid)
            if headcode:
                # Note: VSTP schedules don't have headcode field directly, but we can filter by UID pattern
                sql += " AND uid LIKE ?"
                params.append(f"%{headcode}%")
            if status:
                sql += " AND train_status LIKE ?"
                params.append(f"%{status}%")
            if category:
                sql += " AND CIF_train_category LIKE ?"
                params.append(f"%{category}%")
            if start_date_from:
                sql += " AND schedule_start_date >= ?"
                params.append(start_date_from)
            if start_date_to:
                sql += " AND schedule_start_date <= ?"
                params.append(start_date_to)
            
            # Get total count for pagination
            count_sql = f"SELECT COUNT(*) as total FROM ({sql})"
            total_count = q(count_sql, params)[0]['total']
            total_pages = (total_count + per_page - 1) // per_page
            
            sql += f" ORDER BY {sort_by} {sort_dir} LIMIT ? OFFSET ?"
            params.extend([per_page, offset])
            
            try:
                rows = q(sql, params)
                if rows:
                    body.append(f"<p class='dim'>Showing {len(rows)} of {total_count} schedule(s) (Page {page} of {total_pages}). Click UID to see locations.</p>")
                    
                    # Add sorting helper function
                    def sort_link(column, label):
                        # Preserve all current query params
                        params_dict = {
                            'view': 'schedules',
                            'uid': uid,
                            'headcode': headcode,
                            'status': status,
                            'category': category,
                            'start_date_from': start_date_from,
                            'start_date_to': start_date_to,
                            'page': str(page),
                            'sort_by': column,
                            'sort_dir': 'ASC' if sort_by == column and sort_dir == 'DESC' else 'DESC'
                        }
                        # Remove empty params
                        params_dict = {k: v for k, v in params_dict.items() if v}
                        arrow = ""
                        if sort_by == column:
                            arrow = " ↓" if sort_dir == "DESC" else " ↑"
                        return f"<a href='/vstp?{urlencode(params_dict)}' style='color:inherit;text-decoration:none'>{label}{arrow}</a>"
                    
                    body.append("<table>")
                    body.append(f"<tr><th>{sort_link('uid', 'UID')}</th><th>{sort_link('schedule_start_date', 'Start Date')}</th><th>{sort_link('schedule_end_date', 'End Date')}</th><th>{sort_link('transaction_type', 'Type')}</th><th>{sort_link('train_status', 'Status')}</th><th>Signalling ID</th><th>Service Code</th><th>{sort_link('CIF_train_category', 'Category')}</th><th>Power</th><th>{sort_link('created_at_utc', 'Created')}</th></tr>")
                    for r in rows:
                        # Create a detail link with all current filters
                        detail_params = {k: v for k, v in request.args.items() if k not in ['page', 'detail']}
                        detail_params['detail'] = '1'
                        detail_params['uid'] = r['uid']
                        detail_url = f"/vstp?{urlencode(detail_params)}"
                        body.append(
                            f"<tr>"
                            f"<td><a href='{detail_url}'><b>{r['uid']}</b></a></td>"
                            f"<td class='mono'>{r['schedule_start_date']}</td>"
                            f"<td class='mono'>{r['schedule_end_date'] or ''}</td>"
                            f"<td>{r['transaction_type'] or ''}</td>"
                            f"<td>{r['train_status'] or ''}</td>"
                            f"<td class='mono'>{r['signalling_id'] or ''}</td>"
                            f"<td>{r['CIF_train_service_code'] or ''}</td>"
                            f"<td>{r['CIF_train_category'] or ''}</td>"
                            f"<td>{r['CIF_power_type'] or ''}</td>"
                            f"<td class='mono dim'>{r['created_at_utc'][:19] if r['created_at_utc'] else ''}</td>"
                            f"</tr>"
                        )
                    body.append("</table>")
                    
                    # Add pagination controls
                    if total_pages > 1:
                        body.append("<div style='margin:20px 0;text-align:center'>")
                        # Preserve filter params in pagination
                        page_params = {k: v for k, v in request.args.items() if k != 'page'}
                        
                        if page > 1:
                            page_params['page'] = str(page - 1)
                            body.append(f"<a href='/vstp?{urlencode(page_params)}' style='padding:8px 12px;margin:0 4px;background:#0b5cff;color:white;text-decoration:none;border-radius:4px'>← Previous</a>")
                        
                        # Show page numbers
                        start_page = max(1, page - 2)
                        end_page = min(total_pages, page + 2)
                        
                        for p in range(start_page, end_page + 1):
                            if p == page:
                                body.append(f"<span style='padding:8px 12px;margin:0 4px;background:#333;color:white;border-radius:4px'>{p}</span>")
                            else:
                                page_params['page'] = str(p)
                                body.append(f"<a href='/vstp?{urlencode(page_params)}' style='padding:8px 12px;margin:0 4px;background:#eee;color:#333;text-decoration:none;border-radius:4px'>{p}</a>")
                        
                        if page < total_pages:
                            page_params['page'] = str(page + 1)
                            body.append(f"<a href='/vstp?{urlencode(page_params)}' style='padding:8px 12px;margin:0 4px;background:#0b5cff;color:white;text-decoration:none;border-radius:4px'>Next →</a>")
                        
                        body.append("</div>")
                    
                    # If detail is requested, show locations for the selected schedule
                    if uid and request.args.get("detail"):
                        body.append(f"<h3>Locations for Schedule {uid}</h3>")
                        # Get schedule_start_date from the schedule row first
                        schedule_row = q("SELECT schedule_start_date FROM vstp_schedules WHERE uid=? ORDER BY created_at_utc DESC LIMIT 1", (uid,))
                        if schedule_row:
                            schedule_start_date = schedule_row[0]['schedule_start_date']
                            loc_sql = """SELECT tiploc, scheduled_pass_time, scheduled_departure_time, 
                                         scheduled_arrival_time, public_departure_time, public_arrival_time,
                                         CIF_pathing_allowance, CIF_activity, CIF_line
                                         FROM vstp_schedule_locations 
                                         WHERE uid=? AND schedule_start_date=?
                                         ORDER BY segment_index, location_index"""
                            loc_rows = q(loc_sql, (uid, schedule_start_date))
                            
                            if loc_rows:
                                body.append("<table>")
                                body.append("<tr><th>TIPLOC</th><th>Pass Time</th><th>Departure</th><th>Arrival</th><th>Public Dep</th><th>Public Arr</th><th>Pathing</th><th>Activity</th><th>Line</th></tr>")
                                for loc in loc_rows:
                                    body.append(
                                        f"<tr>"
                                        f"<td class='mono'>{loc['tiploc'] or ''}</td>"
                                        f"<td class='mono'>{loc['scheduled_pass_time'] or ''}</td>"
                                        f"<td class='mono'>{loc['scheduled_departure_time'] or ''}</td>"
                                        f"<td class='mono'>{loc['scheduled_arrival_time'] or ''}</td>"
                                        f"<td class='mono'>{loc['public_departure_time'] or ''}</td>"
                                        f"<td class='mono'>{loc['public_arrival_time'] or ''}</td>"
                                        f"<td>{loc['CIF_pathing_allowance'] or ''}</td>"
                                        f"<td>{loc['CIF_activity'] or ''}</td>"
                                        f"<td>{loc['CIF_line'] or ''}</td>"
                                        f"</tr>"
                                    )
                                body.append("</table>")
                            else:
                                body.append("<p><i>No locations found for this schedule</i></p>")
                        else:
                            body.append("<p><i>Schedule not found</i></p>")
                else:
                    body.append("<p><i>No VSTP schedules found</i></p>")
            except Exception as e:
                logger.error(f"Web dashboard: Error querying vstp_schedules: {e}")
                body.append(f"<p><i>Error querying vstp_schedules: {e}</i></p>")
        
        return render_page("VSTP - NR RailHub", body, active="vstp")

    @app.get("/cif")
    def cif():
        """Display CIF schedules with filtering, sorting, and pagination."""
        # Get filter parameters
        uid = request.args.get("uid", "").strip()
        headcode = request.args.get("headcode", "").strip()
        toc_code = request.args.get("toc_code", "").strip()
        status = request.args.get("status", "").strip()
        category = request.args.get("category", "").strip()
        start_date_from = request.args.get("start_date_from", "").strip()
        start_date_to = request.args.get("start_date_to", "").strip()
        
        # Get sorting parameters
        sort_by = request.args.get("sort_by", "created_at_utc").strip()
        sort_dir = request.args.get("sort_dir", "DESC").strip().upper()
        if sort_dir not in ["ASC", "DESC"]:
            sort_dir = "DESC"
        
        # Validate sort_by to prevent SQL injection
        valid_sort_columns = ["uid", "schedule_start_date", "schedule_end_date", "toc_code",
                              "transaction_type", "train_status", "created_at_utc", "CIF_train_category",
                              "CIF_headcode"]
        if sort_by not in valid_sort_columns:
            sort_by = "created_at_utc"
        
        # Pagination
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            page = 1
        per_page = 50
        offset = (page - 1) * per_page
        
        body = []
        body.append("<h2>CIF Schedules</h2>")
        body.append("<p>Schedules loaded from daily Train Operating Company (TOC) downloads.</p>")
        
        # Add filter form
        body.append("<div style='background:#f7f9fc;padding:15px;border-radius:8px;margin:15px 0'>")
        body.append("<form method='get' action='/cif' style='display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px'>")
        body.append(f"<div><label style='font-size:12px;color:#666'>UID:</label><br/><input type='text' name='uid' value='{html.escape(uid)}' placeholder='Schedule UID' style='width:100%;padding:6px;border:1px solid #ddd;border-radius:4px'/></div>")
        body.append(f"<div><label style='font-size:12px;color:#666'>Headcode:</label><br/><input type='text' name='headcode' value='{html.escape(headcode)}' placeholder='Train headcode' style='width:100%;padding:6px;border:1px solid #ddd;border-radius:4px'/></div>")
        body.append(f"<div><label style='font-size:12px;color:#666'>TOC Code:</label><br/><input type='text' name='toc_code' value='{html.escape(toc_code)}' placeholder='TOC code' style='width:100%;padding:6px;border:1px solid #ddd;border-radius:4px'/></div>")
        body.append(f"<div><label style='font-size:12px;color:#666'>Status:</label><br/><input type='text' name='status' value='{html.escape(status)}' placeholder='Train status' style='width:100%;padding:6px;border:1px solid #ddd;border-radius:4px'/></div>")
        body.append(f"<div><label style='font-size:12px;color:#666'>Category:</label><br/><input type='text' name='category' value='{html.escape(category)}' placeholder='Category' style='width:100%;padding:6px;border:1px solid #ddd;border-radius:4px'/></div>")
        body.append(f"<div><label style='font-size:12px;color:#666'>Start Date From:</label><br/><input type='date' name='start_date_from' value='{html.escape(start_date_from)}' style='width:100%;padding:6px;border:1px solid #ddd;border-radius:4px'/></div>")
        body.append(f"<div><label style='font-size:12px;color:#666'>Start Date To:</label><br/><input type='date' name='start_date_to' value='{html.escape(start_date_to)}' style='width:100%;padding:6px;border:1px solid #ddd;border-radius:4px'/></div>")
        body.append("<div style='grid-column:span 2'><button type='submit' style='padding:8px 16px;background:#0b5cff;color:white;border:0;border-radius:4px;cursor:pointer;margin-right:8px'>Apply Filters</button>")
        body.append("<a href='/cif' style='padding:8px 16px;background:#eee;color:#333;border:0;border-radius:4px;text-decoration:none;display:inline-block'>Clear Filters</a></div>")
        body.append("</form></div>")
        
        sql = """SELECT uid, schedule_start_date, schedule_end_date, toc_code, transaction_type, 
                 train_status, signalling_id, CIF_train_service_code, CIF_train_category, 
                 CIF_power_type, CIF_headcode, CIF_stp_indicator, created_at_utc 
                 FROM cif_schedules WHERE 1=1"""
        params = []
        
        if uid:
            sql += " AND uid=?"
            params.append(uid)
        if headcode:
            sql += " AND CIF_headcode LIKE ?"
            params.append(f"%{headcode}%")
        if toc_code:
            sql += " AND toc_code=?"
            params.append(toc_code)
        if status:
            sql += " AND train_status LIKE ?"
            params.append(f"%{status}%")
        if category:
            sql += " AND CIF_train_category LIKE ?"
            params.append(f"%{category}%")
        if start_date_from:
            sql += " AND schedule_start_date >= ?"
            params.append(start_date_from)
        if start_date_to:
            sql += " AND schedule_start_date <= ?"
            params.append(start_date_to)
        
        # Get total count for pagination
        count_sql = f"SELECT COUNT(*) as total FROM ({sql})"
        try:
            total_count = q(count_sql, params)[0]['total']
        except Exception as e:
            logger.error(f"Web dashboard: Error counting cif_schedules: {e}")
            total_count = 0
        
        total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 1
        
        sql += f" ORDER BY {sort_by} {sort_dir} LIMIT ? OFFSET ?"
        params.extend([per_page, offset])
        
        try:
            rows = q(sql, params)
            if rows:
                body.append(f"<p class='dim'>Showing {len(rows)} of {total_count} schedule(s) (Page {page} of {total_pages}). Click UID to see locations.</p>")
                
                # Add sorting helper function
                def sort_link(column, label):
                    # Preserve all current query params
                    params_dict = {
                        'uid': uid,
                        'headcode': headcode,
                        'toc_code': toc_code,
                        'status': status,
                        'category': category,
                        'start_date_from': start_date_from,
                        'start_date_to': start_date_to,
                        'page': str(page),
                        'sort_by': column,
                        'sort_dir': 'ASC' if sort_by == column and sort_dir == 'DESC' else 'DESC'
                    }
                    # Remove empty params
                    params_dict = {k: v for k, v in params_dict.items() if v}
                    arrow = ""
                    if sort_by == column:
                        arrow = " ↓" if sort_dir == "DESC" else " ↑"
                    return f"<a href='/cif?{urlencode(params_dict)}' style='color:inherit;text-decoration:none'>{label}{arrow}</a>"
                
                body.append("<table>")
                body.append(f"<tr><th>{sort_link('uid', 'UID')}</th><th>{sort_link('CIF_headcode', 'Headcode')}</th><th>{sort_link('toc_code', 'TOC')}</th><th>{sort_link('schedule_start_date', 'Start Date')}</th><th>{sort_link('schedule_end_date', 'End Date')}</th><th>{sort_link('transaction_type', 'Type')}</th><th>{sort_link('train_status', 'Status')}</th><th>{sort_link('CIF_train_category', 'Category')}</th><th>Power</th><th>STP</th><th>{sort_link('created_at_utc', 'Created')}</th></tr>")
                for r in rows:
                    # Create a detail link with all current filters
                    detail_params = {k: v for k, v in request.args.items() if k not in ['page', 'detail']}
                    detail_params['detail'] = '1'
                    detail_params['uid'] = r['uid']
                    detail_url = f"/cif?{urlencode(detail_params)}"
                    body.append(
                        f"<tr>"
                        f"<td><a href='{detail_url}'><b>{r['uid']}</b></a></td>"
                        f"<td class='mono'><b>{r['CIF_headcode'] or ''}</b></td>"
                        f"<td class='mono'>{r['toc_code'] or ''}</td>"
                        f"<td class='mono'>{r['schedule_start_date']}</td>"
                        f"<td class='mono'>{r['schedule_end_date'] or ''}</td>"
                        f"<td>{r['transaction_type'] or ''}</td>"
                        f"<td>{r['train_status'] or ''}</td>"
                        f"<td>{r['CIF_train_category'] or ''}</td>"
                        f"<td>{r['CIF_power_type'] or ''}</td>"
                        f"<td>{r['CIF_stp_indicator'] or ''}</td>"
                        f"<td class='mono dim'>{r['created_at_utc'][:19] if r['created_at_utc'] else ''}</td>"
                        f"</tr>"
                    )
                body.append("</table>")
                
                # Add pagination controls
                if total_pages > 1:
                    body.append("<div style='margin:20px 0;text-align:center'>")
                    # Preserve filter params in pagination
                    page_params = {k: v for k, v in request.args.items() if k != 'page'}
                    
                    if page > 1:
                        page_params['page'] = str(page - 1)
                        body.append(f"<a href='/cif?{urlencode(page_params)}' style='padding:8px 12px;margin:0 4px;background:#0b5cff;color:white;text-decoration:none;border-radius:4px'>← Previous</a>")
                    
                    # Show page numbers
                    start_page = max(1, page - 2)
                    end_page = min(total_pages, page + 2)
                    
                    for p in range(start_page, end_page + 1):
                        if p == page:
                            body.append(f"<span style='padding:8px 12px;margin:0 4px;background:#333;color:white;border-radius:4px'>{p}</span>")
                        else:
                            page_params['page'] = str(p)
                            body.append(f"<a href='/cif?{urlencode(page_params)}' style='padding:8px 12px;margin:0 4px;background:#eee;color:#333;text-decoration:none;border-radius:4px'>{p}</a>")
                    
                    if page < total_pages:
                        page_params['page'] = str(page + 1)
                        body.append(f"<a href='/cif?{urlencode(page_params)}' style='padding:8px 12px;margin:0 4px;background:#0b5cff;color:white;text-decoration:none;border-radius:4px'>Next →</a>")
                    
                    body.append("</div>")
                
                # If detail is requested, show locations for the selected schedule
                if uid and request.args.get("detail"):
                    body.append(f"<h3>Locations for Schedule {uid}</h3>")
                    # Get schedule_start_date and stp_indicator from the schedule row first
                    schedule_row = q("SELECT schedule_start_date, CIF_stp_indicator FROM cif_schedules WHERE uid=? ORDER BY created_at_utc DESC LIMIT 1", (uid,))
                    if schedule_row:
                        schedule_start_date = schedule_row[0]['schedule_start_date']
                        loc_sql = """SELECT tiploc, scheduled_pass_time, scheduled_departure_time, 
                                     scheduled_arrival_time, public_departure_time, public_arrival_time,
                                     platform, CIF_pathing_allowance, CIF_activity, CIF_line
                                     FROM cif_schedule_locations 
                                     WHERE uid=? AND schedule_start_date=?
                                     ORDER BY segment_index, location_index"""
                        loc_rows = q(loc_sql, (uid, schedule_start_date))
                        
                        if loc_rows:
                            body.append("<table>")
                            body.append("<tr><th>TIPLOC</th><th>Pass Time</th><th>Departure</th><th>Arrival</th><th>Public Dep</th><th>Public Arr</th><th>Platform</th><th>Pathing</th><th>Activity</th><th>Line</th></tr>")
                            for loc in loc_rows:
                                body.append(
                                    f"<tr>"
                                    f"<td class='mono'>{loc['tiploc'] or ''}</td>"
                                    f"<td class='mono'>{loc['scheduled_pass_time'] or ''}</td>"
                                    f"<td class='mono'>{loc['scheduled_departure_time'] or ''}</td>"
                                    f"<td class='mono'>{loc['scheduled_arrival_time'] or ''}</td>"
                                    f"<td class='mono'>{loc['public_departure_time'] or ''}</td>"
                                    f"<td class='mono'>{loc['public_arrival_time'] or ''}</td>"
                                    f"<td class='mono'>{loc['platform'] or ''}</td>"
                                    f"<td>{loc['CIF_pathing_allowance'] or ''}</td>"
                                    f"<td>{loc['CIF_activity'] or ''}</td>"
                                    f"<td>{loc['CIF_line'] or ''}</td>"
                                    f"</tr>"
                                )
                            body.append("</table>")
                        else:
                            body.append("<p><i>No locations found for this schedule</i></p>")
                    else:
                        body.append("<p><i>Schedule not found</i></p>")
            else:
                body.append("<p><i>No CIF schedules found. Schedules are loaded from daily TOC downloads. Check if the application has downloaded TOC schedules.</i></p>")
        except Exception as e:
            logger.error(f"Web dashboard: Error querying cif_schedules: {e}")
            body.append(f"<p><i>Error querying cif_schedules: {e}</i></p>")
        
        return render_page("CIF Schedules - NR RailHub", body, active="cif")

    @app.get("/tocs")
    def tocs():
        """Display Train Operating Company (TOC) reference data."""
        body = ["<h2>Train Operating Companies (TOCs)</h2>"]
        body.append("<p>Reference data for UK train operating companies. TOC codes are used in TRUST messages.</p>")
        
        try:
            rows = q("SELECT toc_name, toc_code, business_code, sector_code, atoc_code, sector, updated_at_utc FROM toc_reference ORDER BY toc_name")
            if rows:
                body.append(f"<p class='dim'>Showing {len(rows)} TOC(s)</p>")
                
                # Add search filter box
                body.append("<div style='margin:10px 0'><input type='text' id='tableFilter' placeholder='Filter by TOC name, code, sector...' style='padding:8px;width:300px;border:1px solid #ccc;border-radius:4px'/> <span id='filterCount' style='margin-left:10px;color:#6c757d'></span></div>")
                
                # Table with sortable headers
                body.append("<table id='tocTable'><thead><tr>")
                body.append("<th onclick='sortTable(0)' style='cursor:pointer'>TOC Name <span id='sort0'></span></th>")
                body.append("<th onclick='sortTable(1)' style='cursor:pointer'>TOC Code <span id='sort1'></span></th>")
                body.append("<th onclick='sortTable(2)' style='cursor:pointer'>Business Code <span id='sort2'></span></th>")
                body.append("<th onclick='sortTable(3)' style='cursor:pointer'>Sector Code <span id='sort3'></span></th>")
                body.append("<th onclick='sortTable(4)' style='cursor:pointer'>ATOC Code <span id='sort4'></span></th>")
                body.append("<th onclick='sortTable(5)' style='cursor:pointer'>Sector <span id='sort5'></span></th>")
                body.append("<th onclick='sortTable(6)' style='cursor:pointer'>Last Updated <span id='sort6'></span></th>")
                body.append("</tr></thead><tbody>")
                
                for r in rows:
                    body.append("<tr>")
                    body.append(f"<td><b>{r['toc_name']}</b></td>")
                    body.append(f"<td class='mono'>{r['toc_code']}</td>")
                    body.append(f"<td class='mono'>{r['business_code'] or ''}</td>")
                    body.append(f"<td class='mono'>{r['sector_code'] or ''}</td>")
                    body.append(f"<td class='mono'>{r['atoc_code'] or ''}</td>")
                    body.append(f"<td>{r['sector'] or ''}</td>")
                    body.append(f"<td class='mono dim'>{r['updated_at_utc'][:19] if r['updated_at_utc'] else ''}</td>")
                    body.append("</tr>")
                
                body.append("</tbody></table>")
                
                # Add JavaScript for sorting and filtering
                body.append("""
<script>
// Sorting functionality
let sortDirection = {};
function sortTable(columnIndex) {
    const table = document.getElementById('tocTable');
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    
    // Toggle sort direction
    sortDirection[columnIndex] = !sortDirection[columnIndex];
    const ascending = sortDirection[columnIndex];
    
    // Clear all sort indicators
    const numColumns = table.querySelector('thead tr').cells.length;
    for (let i = 0; i < numColumns; i++) {
        const sortSpan = document.getElementById('sort' + i);
        if (sortSpan) sortSpan.textContent = '';
    }
    
    // Set current sort indicator
    const currentSortSpan = document.getElementById('sort' + columnIndex);
    if (currentSortSpan) currentSortSpan.textContent = ascending ? ' ▲' : ' ▼';
    
    // Sort rows
    rows.sort((a, b) => {
        let aVal = a.cells[columnIndex].textContent.trim();
        let bVal = b.cells[columnIndex].textContent.trim();
        
        // Handle timestamps (ISO format YYYY-MM-DD...)
        if (columnIndex === 6 && aVal && bVal) {
            return ascending ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
        }
        
        // Handle empty values
        if (!aVal) return ascending ? 1 : -1;
        if (!bVal) return ascending ? -1 : 1;
        
        // Default: case-insensitive string comparison
        const comparison = aVal.toLowerCase().localeCompare(bVal.toLowerCase());
        return ascending ? comparison : -comparison;
    });
    
    // Re-append sorted rows
    rows.forEach(row => tbody.appendChild(row));
}

// Filtering functionality
const filterInput = document.getElementById('tableFilter');
const filterCount = document.getElementById('filterCount');
const table = document.getElementById('tocTable');
const tbody = table.querySelector('tbody');
const allRows = Array.from(tbody.querySelectorAll('tr'));

function updateFilter() {
    const filterText = filterInput.value.toLowerCase();
    let visibleCount = 0;
    
    allRows.forEach(row => {
        const text = row.textContent.toLowerCase();
        if (text.includes(filterText)) {
            row.style.display = '';
            visibleCount++;
        } else {
            row.style.display = 'none';
        }
    });
    
    if (filterText) {
        filterCount.textContent = `Showing ${visibleCount} of ${allRows.length} rows`;
    } else {
        filterCount.textContent = '';
    }
}

filterInput.addEventListener('input', updateFilter);
</script>
""")
            else:
                body.append("<p><i>No TOC reference data found. Make sure the application has been started with a database path.</i></p>")
        except Exception as e:
            logger.error(f"Web dashboard: Error querying toc_reference: {e}")
            body.append(f"<p><i>Error querying toc_reference: {e}</i></p>")
        
        return render_page("TOCs - NR RailHub", body, active="tocs")

    @app.route("/toc-td-areas", methods=["GET", "POST"])
    def toc_td_areas():
        """Admin page for managing TOC-TD area mappings."""
        
        # Handle POST requests (add or delete mappings)
        if request.method == "POST":
            action = request.form.get("action", "").strip()
            
            if action == "add":
                toc_code = request.form.get("toc_code", "").strip().upper()
                td_area = request.form.get("td_area", "").strip().upper()
                is_primary = request.form.get("is_primary") == "on"
                notes = request.form.get("notes", "").strip()
                
                if toc_code and td_area:
                    try:
                        q(
                            """
                            INSERT INTO toc_td_areas(toc_code, td_area, is_primary, source, created_by, notes)
                            VALUES (?, ?, ?, 'web_ui', 'admin', ?)
                            ON CONFLICT(toc_code, td_area) DO UPDATE SET
                                is_primary=excluded.is_primary,
                                notes=excluded.notes,
                                created_at_ts=strftime('%s','now') * 1000
                            """,
                            (toc_code, td_area, 1 if is_primary else 0, notes if notes else None)
                        )
                        logger.info(f"Added/updated TOC-TD mapping: {toc_code} <-> {td_area}")
                    except Exception as e:
                        logger.error(f"Error adding TOC-TD mapping: {e}")
                        return render_page("Error - NR RailHub", [f"<p>Error adding mapping: {e}</p>"], active="")
                
                return redirect("/toc-td-areas")
            
            elif action == "delete":
                toc_code = request.form.get("toc_code", "").strip()
                td_area = request.form.get("td_area", "").strip()
                
                if toc_code and td_area:
                    try:
                        q("DELETE FROM toc_td_areas WHERE toc_code=? AND td_area=?", (toc_code, td_area))
                        logger.info(f"Deleted TOC-TD mapping: {toc_code} <-> {td_area}")
                    except Exception as e:
                        logger.error(f"Error deleting TOC-TD mapping: {e}")
                
                return redirect("/toc-td-areas")
        
        # Handle GET request (display page)
        body = ["<h2>TOC ↔ TD Area Mappings</h2>"]
        body.append("<p>Manage many-to-many relationships between Train Operating Companies and TD areas. ")
        body.append("These mappings help constrain candidate schedules when matching berth events to trains.</p>")
        
        # Add new mapping form
        body.append("<div style='background:#f7f9fc;padding:15px;margin:20px 0;border-radius:6px'>")
        body.append("<h3 style='margin-top:0'>Add New Mapping</h3>")
        body.append("<form method='post' action='/toc-td-areas'>")
        body.append("<input type='hidden' name='action' value='add'/>")
        
        # Get available TOCs for dropdown
        try:
            toc_rows = q("SELECT toc_code, toc_name FROM toc_reference ORDER BY toc_code")
            body.append("<label>TOC: <select name='toc_code' required style='padding:6px;margin:0 10px 0 5px'>")
            body.append("<option value=''>Select TOC...</option>")
            for toc_row in toc_rows:
                body.append(f"<option value='{toc_row['toc_code']}'>{toc_row['toc_code']} - {toc_row['toc_name']}</option>")
            body.append("</select></label>")
        except Exception as e:
            logger.error(f"Error loading TOCs: {e}")
            body.append("<label>TOC: <input name='toc_code' required placeholder='e.g., SW' style='padding:6px;margin:0 10px 0 5px' size='4'/></label>")
        
        body.append("<label>TD Area: <input name='td_area' required placeholder='e.g., EK' style='padding:6px;margin:0 10px 0 5px' size='4' maxlength='2'/></label>")
        body.append("<label><input type='checkbox' name='is_primary'/> Primary</label>")
        body.append("<label style='margin-left:10px'>Notes: <input name='notes' placeholder='Optional notes' style='padding:6px;width:200px'/></label>")
        body.append("<button type='submit' style='padding:6px 12px;margin-left:10px;background:#0b5cff;color:white;border:0;border-radius:4px;cursor:pointer'>Add Mapping</button>")
        body.append("</form>")
        body.append("</div>")
        
        # Display current mappings
        try:
            rows = q("""
                SELECT toc_code, td_area, is_primary, source, confidence, notes, created_at_ts
                FROM toc_td_areas
                ORDER BY toc_code, td_area
            """)
            
            if rows:
                body.append(f"<p class='dim'>Showing {len(rows)} mapping(s)</p>")
                
                # Add search filter box
                body.append("<div style='margin:10px 0'><input type='text' id='tableFilter' placeholder='Filter by TOC, TD area...' style='padding:8px;width:300px;border:1px solid #ccc;border-radius:4px'/> <span id='filterCount' style='margin-left:10px;color:#6c757d'></span></div>")
                
                # Table with sortable headers
                body.append("<table id='mappingTable'><thead><tr>")
                body.append("<th onclick='sortTable(0)' style='cursor:pointer'>TOC <span id='sort0'></span></th>")
                body.append("<th onclick='sortTable(1)' style='cursor:pointer'>TD Area <span id='sort1'></span></th>")
                body.append("<th onclick='sortTable(2)' style='cursor:pointer'>Primary <span id='sort2'></span></th>")
                body.append("<th onclick='sortTable(3)' style='cursor:pointer'>Source <span id='sort3'></span></th>")
                body.append("<th onclick='sortTable(4)' style='cursor:pointer'>Confidence <span id='sort4'></span></th>")
                body.append("<th>Notes</th>")
                body.append("<th>Action</th>")
                body.append("</tr></thead><tbody>")
                
                for r in rows:
                    body.append("<tr>")
                    body.append(f"<td class='mono'><b>{r['toc_code']}</b></td>")
                    body.append(f"<td class='mono'><b>{r['td_area']}</b></td>")
                    body.append(f"<td>{'Yes' if r['is_primary'] else 'No'}</td>")
                    body.append(f"<td>{r['source'] or ''}</td>")
                    body.append(f"<td>{r['confidence'] if r['confidence'] is not None else ''}</td>")
                    body.append(f"<td>{r['notes'] or ''}</td>")
                    body.append("<td>")
                    body.append("<form method='post' action='/toc-td-areas' style='display:inline'>")
                    body.append("<input type='hidden' name='action' value='delete'/>")
                    body.append(f"<input type='hidden' name='toc_code' value='{r['toc_code']}'/>")
                    body.append(f"<input type='hidden' name='td_area' value='{r['td_area']}'/>")
                    body.append("<button type='submit' onclick='return confirm(\"Delete this mapping?\")' style='padding:4px 8px;background:#dc3545;color:white;border:0;border-radius:4px;cursor:pointer;font-size:12px'>Delete</button>")
                    body.append("</form>")
                    body.append("</td>")
                    body.append("</tr>")
                
                body.append("</tbody></table>")
                
                # Add JavaScript for sorting and filtering
                body.append("""
<script>
// Sorting functionality
let sortDirection = {};
function sortTable(columnIndex) {
    const table = document.getElementById('mappingTable');
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    
    // Toggle sort direction
    sortDirection[columnIndex] = !sortDirection[columnIndex];
    const ascending = sortDirection[columnIndex];
    
    // Clear all sort indicators
    for (let i = 0; i < 5; i++) {
        const sortSpan = document.getElementById('sort' + i);
        if (sortSpan) sortSpan.textContent = '';
    }
    
    // Set current sort indicator
    const currentSortSpan = document.getElementById('sort' + columnIndex);
    if (currentSortSpan) currentSortSpan.textContent = ascending ? ' ▲' : ' ▼';
    
    // Sort rows
    rows.sort((a, b) => {
        let aVal = a.cells[columnIndex].textContent.trim();
        let bVal = b.cells[columnIndex].textContent.trim();
        
        // Handle empty values
        if (!aVal) return ascending ? 1 : -1;
        if (!bVal) return ascending ? -1 : 1;
        
        // Default: case-insensitive string comparison
        const comparison = aVal.toLowerCase().localeCompare(bVal.toLowerCase());
        return ascending ? comparison : -comparison;
    });
    
    // Re-append sorted rows
    rows.forEach(row => tbody.appendChild(row));
}

// Filtering functionality
const filterInput = document.getElementById('tableFilter');
const filterCount = document.getElementById('filterCount');
const table = document.getElementById('mappingTable');
const tbody = table.querySelector('tbody');
const allRows = Array.from(tbody.querySelectorAll('tr'));

function updateFilter() {
    const filterText = filterInput.value.toLowerCase();
    let visibleCount = 0;
    
    allRows.forEach(row => {
        const text = row.textContent.toLowerCase();
        if (text.includes(filterText)) {
            row.style.display = '';
            visibleCount++;
        } else {
            row.style.display = 'none';
        }
    });
    
    if (filterText) {
        filterCount.textContent = `Showing ${visibleCount} of ${allRows.length} rows`;
    } else {
        filterCount.textContent = '';
    }
}

filterInput.addEventListener('input', updateFilter);
</script>
""")
            else:
                body.append("<p><i>No TOC-TD area mappings found. Use the form above to add your first mapping.</i></p>")
        except Exception as e:
            logger.error(f"Web dashboard: Error querying toc_td_areas: {e}")
            body.append(f"<p><i>Error querying toc_td_areas: {e}</i></p>")
        
        return render_page("TOC-TD Areas - NR RailHub", body, active="toc-td-areas")

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
            logger.error(f"Web dashboard: Error querying events: {e}")
            body.append(f"<p><i>Error querying events: {e}</i></p>")
        return render_page("Raw Events - NR RailHub", body, active="raw")



    # --- API endpoints: time-series data for charts ---
    
    @app.get("/api/stats/messages-per-second")
    def api_messages_per_second():
        """
        Returns JSON:
          { "labels": [ISO timestamps], "data": [counts per second] }
        Last 5 minutes, grouped per-second.
        """
        try:
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - (5 * 60 * 1000)  # last 5 minutes
            sql = """
                SELECT ((ts_ms / 1000) * 1000) AS sec_ts, COUNT(*) AS cnt
                FROM (
                    SELECT ts_ms FROM td_signal_events WHERE ts_ms >= ?
                    UNION ALL
                    SELECT ts_ms FROM td_berth_events WHERE ts_ms >= ?
                )
                GROUP BY sec_ts
                ORDER BY sec_ts
            """
            rows = q(sql, (start_ms, start_ms))
            labels = []
            data = []
            # Build a full per-second array (fill missing seconds with 0)
            sec_points = {}
            for r in rows:
                sec_points[int(r["sec_ts"])] = int(r["cnt"])
            for s in range(int(start_ms / 1000), int(now_ms / 1000) + 1):
                ts_ms = s * 1000
                labels.append(datetime.utcfromtimestamp(s).isoformat() + "Z")
                data.append(sec_points.get(ts_ms, 0))
            return json.dumps({"labels": labels, "data": data})
        except Exception as e:
            logger.error(f"API messages-per-second error: {e}")
            return json.dumps({"error": str(e)})
    
    @app.get("/api/stats/records-per-minute")
    def api_records_per_minute():
        """
        Returns JSON:
          { "labels": [ISO timestamps], "data": [counts per minute] }
        Last 60 minutes, grouped per-minute (berth+signal).
        """
        try:
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - (60 * 60 * 1000)  # last 60 minutes
            sql = """
                SELECT ((ts_ms / 60000) * 60000) AS minute_ts, COUNT(*) AS cnt
                FROM (
                    SELECT ts_ms FROM td_signal_events WHERE ts_ms >= ?
                    UNION ALL
                    SELECT ts_ms FROM td_berth_events WHERE ts_ms >= ?
                )
                GROUP BY minute_ts
                ORDER BY minute_ts
            """
            rows = q(sql, (start_ms, start_ms))
            minute_points = {int(r["minute_ts"]): int(r["cnt"]) for r in rows}
            labels = []
            data = []
            start_min = int(start_ms / 60000)
            end_min = int(now_ms / 60000)
            for m in range(start_min, end_min + 1):
                ts_ms = m * 60000
                labels.append(datetime.utcfromtimestamp(m * 60).isoformat() + "Z")
                data.append(minute_points.get(ts_ms, 0))
            return json.dumps({"labels": labels, "data": data})
        except Exception as e:
            logger.error(f"API records-per-minute error: {e}")
            return json.dumps({"error": str(e)})



    

    # --- Rich /stats page with cards and charts ---
    
    @app.get("/stats")
    def stats():
        body = ["<h2>Stats</h2>"]
        # Inline CSS for simple cards (keeps style self-contained like other pages)
        body.append("""
        <style>
          .stat-row { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:16px; }
          .stat-card { background:#fff; border:1px solid #e6e9ef; padding:12px; border-radius:8px; min-width:220px; flex:1 1 220px; box-shadow: 0 1px 2px rgba(0,0,0,0.03); }
          .stat-card h3 { margin:0 0 8px 0; font-size:14px; }
          .top-list { font-size:13px; margin:0; padding-left:18px; color:#222; }
          .chart-wrap { display:flex; gap:16px; flex-wrap:wrap; }
          .chart-card { flex:1 1 420px; min-width:320px; background:#fff; padding:12px; border-radius:8px; border:1px solid #eee; }
          .dim { color:#666; margin-bottom:8px; }
        </style>
        """)
    
        try:
            # basic totals (existing)
            counts = q("""
                SELECT 
                    (SELECT COUNT(*) FROM td_state) AS td_state,
                    (SELECT COUNT(*) FROM td_berth_events) AS td_berth_events,
                    (SELECT COUNT(*) FROM td_signal_events) AS td_signal_events
            """)[0]
    
            # Top 10 signals by event count
            top_signals = q("""
                SELECT address, COUNT(*) AS cnt
                FROM td_signal_events
                WHERE address IS NOT NULL
                GROUP BY address
                ORDER BY cnt DESC
                LIMIT 10
            """)
    
            # Top 10 berths combining from_berth and to_berth
            top_berths = q("""
                SELECT berth, SUM(cnt) AS total
                FROM (
                    SELECT from_berth AS berth, COUNT(*) AS cnt FROM td_berth_events WHERE from_berth IS NOT NULL GROUP BY from_berth
                    UNION ALL
                    SELECT to_berth AS berth, COUNT(*) AS cnt FROM td_berth_events WHERE to_berth IS NOT NULL GROUP BY to_berth
                )
                GROUP BY berth
                ORDER BY total DESC
                LIMIT 10
            """)
    
            # Top 10 TD areas
            top_areas = q("""
                SELECT td_area, COUNT(*) AS cnt FROM (
                    SELECT td_area FROM td_signal_events
                    UNION ALL
                    SELECT td_area FROM td_berth_events
                ) GROUP BY td_area ORDER BY cnt DESC LIMIT 10
            """)
    
            # Top 10 headcodes (from td_berth_events)
            top_headcodes = q("""
                SELECT headcode, COUNT(*) AS cnt FROM td_berth_events
                WHERE headcode IS NOT NULL
                GROUP BY headcode
                ORDER BY cnt DESC
                LIMIT 10
            """)
    
            # Compose summary cards
            body.append("<div class='stat-row'>")
            body.append(f"<div class='stat-card'><h3>Total tracked trains</h3><div class='dim'>{counts['td_state']}</div></div>")
            body.append(f"<div class='stat-card'><h3>Total berth events</h3><div class='dim'>{counts['td_berth_events']}</div></div>")
            body.append(f"<div class='stat-card'><h3>Total signal events</h3><div class='dim'>{counts['td_signal_events']}</div></div>")
            body.append("</div>")
    
            # Top lists
            body.append("<div class='stat-row'>")
            # Signals
            s_html = "<div class='stat-card'><h3>Top 10 Signal Addresses</h3><ol class='top-list'>"
            for r in top_signals:
                s_html += f"<li>{r['address']} — {r['cnt']}</li>"
            s_html += "</ol></div>"
            body.append(s_html)
            # Berths
            b_html = "<div class='stat-card'><h3>Top 10 Berths</h3><ol class='top-list'>"
            for r in top_berths:
                b_html += f"<li>{r['berth']} — {r['total']}</li>"
            b_html += "</ol></div>"
            body.append(b_html)
            # Areas
            a_html = "<div class='stat-card'><h3>Top 10 Areas</h3><ol class='top-list'>"
            for r in top_areas:
                a_html += f"<li>{r['td_area']} — {r['cnt']}</li>"
            a_html += "</ol></div>"
            body.append(a_html)
            # Headcodes
            h_html = "<div class='stat-card'><h3>Top 10 Headcodes</h3><ol class='top-list'>"
            for r in top_headcodes:
                h_html += f"<li>{r['headcode']} — {r['cnt']}</li>"
            h_html += "</ol></div>"
            body.append(h_html)
    
            body.append("</div>")
    
            # Charts area (Chart.js via CDN)
            body.append("""
            <div class='chart-wrap'>
              <div class='chart-card'>
                <h3>Messages per second (last 5 min)</h3>
                <canvas id='msgRateChart' height='160'></canvas>
              </div>
              <div class='chart-card'>
                <h3>Records inserted per minute (last 60 min)</h3>
                <canvas id='recordsChart' height='160'></canvas>
              </div>
            </div>
    
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <script>
            async function drawCharts() {
              try {
                const m = await fetch('/api/stats/messages-per-second').then(r=>r.json());
                const r = await fetch('/api/stats/records-per-minute').then(r=>r.json());
    
                const ctx1 = document.getElementById('msgRateChart').getContext('2d');
                window.msgRateChart = new Chart(ctx1, {
                  type: 'line',
                  data: {
                    labels: m.labels,
                    datasets: [{ label: 'msgs/sec', data: m.data, borderColor: '#1976d2', backgroundColor:'rgba(25,118,210,0.06)', fill:true, pointRadius:0 }]
                  },
                  options: { responsive:true, scales:{ x:{display:false}, y:{beginAtZero:true} } }
                });
    
                const ctx2 = document.getElementById('recordsChart').getContext('2d');
                window.recordsChart = new Chart(ctx2, {
                  type: 'bar',
                  data: {
                    labels: r.labels,
                    datasets: [{ label: 'records/min', data: r.data, backgroundColor:'#4caf50' }]
                  },
                  options: { responsive:true, scales:{ x:{display:false}, y:{beginAtZero:true} } }
                });
              } catch (err) {
                console.error('Chart draw error', err);
              }
            }
            drawCharts();
            // Optionally refresh charts every 30s
            setInterval(drawCharts, 30 * 1000);
            </script>
            """)
        except Exception as e:
            logger.error(f"Web dashboard: Error fetching stats: {e}")
            body.append(f"<p><i>Error fetching stats: {e}</i></p>")
    
        return render_page("Stats - NR RailHub", body, active="stats")

    @app.route("/mapper", methods=["GET", "POST"])
    def mapper():
        """Redirect to unified configuration page."""
        return redirect("/config", code=302)

    @app.get("/signal-mappings")
    def signal_mappings():
        """Signal mappings enquiry screen showing berth-signal correlations."""
        body = ["<h2>Signal Mappings Enquiry</h2>"]
        
        # Check if mapper tables exist
        try:
            table_check = q("SELECT name FROM sqlite_master WHERE type='table' AND name='berth_signal_scores'")
            if not table_check:
                body.append("<p><i>Signal mappings table not found. Ensure database was created with mapper support enabled.</i></p>")
                return render_page("Signal Mappings - NR RailHub", body, active="signal-mappings")
        except Exception as e:
            logger.error(f"Web dashboard: Error checking signal mappings table: {e}")
            body.append(f"<p><i>Error checking signal mappings table: {e}</i></p>")
            return render_page("Signal Mappings - NR RailHub", body, active="signal-mappings")
        
        # Get filter parameters from query string
        td_area_filter = request.args.get("area", "").strip()
        address_filter = request.args.get("address", "").strip()
        from_berth_filter = request.args.get("from_berth", "").strip()
        to_berth_filter = request.args.get("to_berth", "").strip()
        min_score = request.args.get("min_score", "").strip()
        min_obs = request.args.get("min_obs", "").strip()
        
        # Get sort parameters
        sort_by = request.args.get("sort", "score").strip()
        sort_order = request.args.get("order", "desc").strip().lower()
        if sort_order not in ["asc", "desc"]:
            sort_order = "desc"
        
        # Build SQL query with filters
        # Check if corpus_tiploc table exists for location enrichment
        corpus_exists = False
        try:
            corpus_check = q("SELECT name FROM sqlite_master WHERE type='table' AND name='corpus_tiploc'")
            corpus_exists = len(corpus_check) > 0
        except Exception:
            pass
        
        if corpus_exists:
            sql = """
                SELECT 
                    bss.td_area,
                    bss.address,
                    bss.from_berth,
                    bss.to_berth,
                    bss.score,
                    bss.obs_count,
                    bss.last_seen_ts,
                    bss.last_seen_utc,
                    bss.last_data,
                    ct.nlcdesc as location_name,
                    ct.stanox
                FROM berth_signal_scores bss
                LEFT JOIN corpus_tiploc ct ON CAST(bss.address AS TEXT) = CAST(ct.stanox AS TEXT)
                WHERE 1=1
            """
        else:
            sql = """
                SELECT 
                    td_area,
                    address,
                    from_berth,
                    to_berth,
                    score,
                    obs_count,
                    last_seen_ts,
                    last_seen_utc,
                    last_data,
                    NULL as location_name,
                    address as stanox
                FROM berth_signal_scores
                WHERE 1=1
            """
        params = []
        
        # Use the appropriate table alias based on whether corpus exists
        table_alias = "bss." if corpus_exists else ""
        
        if td_area_filter:
            sql += f" AND {table_alias}td_area = ?"
            params.append(td_area_filter)
        if address_filter:
            sql += f" AND {table_alias}address = ?"
            params.append(address_filter)
        if from_berth_filter:
            # Support wildcard filtering with % or *
            if '%' in from_berth_filter or '*' in from_berth_filter:
                sql += f" AND {table_alias}from_berth LIKE ?"
                params.append(from_berth_filter.replace('*', '%'))
            else:
                sql += f" AND {table_alias}from_berth = ?"
                params.append(from_berth_filter)
        if to_berth_filter:
            # Support wildcard filtering with % or *
            if '%' in to_berth_filter or '*' in to_berth_filter:
                sql += f" AND {table_alias}to_berth LIKE ?"
                params.append(to_berth_filter.replace('*', '%'))
            else:
                sql += f" AND {table_alias}to_berth = ?"
                params.append(to_berth_filter)
        if min_score:
            try:
                sql += f" AND {table_alias}score >= ?"
                params.append(float(min_score))
            except ValueError:
                pass
        if min_obs:
            try:
                sql += f" AND {table_alias}obs_count >= ?"
                params.append(int(min_obs))
            except ValueError:
                pass
        
        # Map sort column names to database columns
        sort_columns = {
            "area": f"{table_alias}td_area",
            "address": f"{table_alias}address",
            "from_berth": f"{table_alias}from_berth",
            "to_berth": f"{table_alias}to_berth",
            "score": f"{table_alias}score",
            "obs_count": f"{table_alias}obs_count",
            "last_seen": f"{table_alias}last_seen_ts",
            "location": "location_name" if corpus_exists else f"{table_alias}address",
        }
        
        # Validate and apply sorting
        if sort_by in sort_columns:
            order_clause = f" ORDER BY {sort_columns[sort_by]} {sort_order.upper()}"
        else:
            order_clause = f" ORDER BY {table_alias}score DESC"
        
        sql += order_clause + " LIMIT 500"
        
        try:
            rows = q(sql, params)
            
            # Get summary statistics
            summary_sql = """
                SELECT 
                    COUNT(*) as total_mappings,
                    COUNT(DISTINCT td_area) as total_areas,
                    COUNT(DISTINCT address) as total_addresses,
                    SUM(obs_count) as total_observations,
                    AVG(score) as avg_score,
                    MAX(score) as max_score
                FROM berth_signal_scores
                WHERE 1=1
            """
            summary_params = []
            if td_area_filter:
                summary_sql += " AND td_area = ?"
                summary_params.append(td_area_filter)
            if address_filter:
                summary_sql += " AND address = ?"
                summary_params.append(address_filter)
            if from_berth_filter:
                # Support wildcard filtering with % or *
                if '%' in from_berth_filter or '*' in from_berth_filter:
                    summary_sql += " AND from_berth LIKE ?"
                    summary_params.append(from_berth_filter.replace('*', '%'))
                else:
                    summary_sql += " AND from_berth = ?"
                    summary_params.append(from_berth_filter)
            if to_berth_filter:
                # Support wildcard filtering with % or *
                if '%' in to_berth_filter or '*' in to_berth_filter:
                    summary_sql += " AND to_berth LIKE ?"
                    summary_params.append(to_berth_filter.replace('*', '%'))
                else:
                    summary_sql += " AND to_berth = ?"
                    summary_params.append(to_berth_filter)
            if min_score:
                try:
                    summary_sql += " AND score >= ?"
                    summary_params.append(float(min_score))
                except ValueError:
                    pass
            if min_obs:
                try:
                    summary_sql += " AND obs_count >= ?"
                    summary_params.append(int(min_obs))
                except ValueError:
                    pass
            
            summary = q(summary_sql, summary_params)[0]
            
            # Get list of areas for quick filter
            areas = q("SELECT DISTINCT td_area FROM berth_signal_scores ORDER BY td_area")
            
            body.append("<p class='dim'>")
            body.append("This screen shows signal address to berth mappings based on observed correlations between TD berth movements and signal events.")
            body.append("</p>")
            
            # Summary statistics
            body.append("<div style='background:#f7f9fc;padding:12px;border-radius:6px;margin:12px 0'>")
            body.append(f"<p style='margin:4px 0'><b>Total Mappings:</b> {summary['total_mappings']} | ")
            body.append(f"<b>TD Areas:</b> {summary['total_areas']} | ")
            body.append(f"<b>Unique Addresses:</b> {summary['total_addresses']} | ")
            body.append(f"<b>Total Observations:</b> {summary['total_observations']}</p>")
            if summary['avg_score']:
                body.append(f"<p style='margin:4px 0'><b>Avg Score:</b> {summary['avg_score']:.3f} | ")
                body.append(f"<b>Max Score:</b> {summary['max_score']:.3f}</p>")
            body.append("</div>")
            
            # Area pills
            body.append("<div style='margin:12px 0'>Quick filter: ")
            for area_row in areas:
                area = area_row[0]
                body.append(f"<a class='pill' href='/signal-mappings?area={area}'>{area}</a>")
            body.append(" <a class='pill' href='/signal-mappings'>ALL</a></div>")
            
            # Filter form
            body.append("<h3>Filters</h3>")
            body.append("<p class='dim' style='margin-bottom:8px'>Use * or % as wildcards in berth fields (e.g., '01*' or '%52')</p>")
            body.append("""
                <form method='get' style='background:#f7f9fc;padding:15px;border-radius:6px;margin-bottom:16px'>
                    <div style='display:grid;grid-template-columns:repeat(3,1fr);gap:12px'>
                        <div>
                            <label style='font-weight:600;display:block;margin-bottom:4px'>TD Area:</label>
                            <input type='text' name='area' value='""" + td_area_filter + """' placeholder='e.g. EK' style='padding:6px;width:100%'>
                        </div>
                        <div>
                            <label style='font-weight:600;display:block;margin-bottom:4px'>Signal Address:</label>
                            <input type='text' name='address' value='""" + address_filter + """' placeholder='e.g. 87701' style='padding:6px;width:100%'>
                        </div>
                        <div>
                            <label style='font-weight:600;display:block;margin-bottom:4px'>From Berth <span style='color:#6c757d;font-weight:400'>(supports wildcards)</span>:</label>
                            <input type='text' name='from_berth' value='""" + from_berth_filter + """' placeholder='e.g. 0152 or 01*' style='padding:6px;width:100%'>
                        </div>
                        <div>
                            <label style='font-weight:600;display:block;margin-bottom:4px'>To Berth <span style='color:#6c757d;font-weight:400'>(supports wildcards)</span>:</label>
                            <input type='text' name='to_berth' value='""" + to_berth_filter + """' placeholder='e.g. 0154 or %54' style='padding:6px;width:100%'>
                        </div>
                        <div>
                            <label style='font-weight:600;display:block;margin-bottom:4px'>Min Score:</label>
                            <input type='number' step='0.01' name='min_score' value='""" + min_score + """' placeholder='e.g. 0.5' style='padding:6px;width:100%'>
                        </div>
                        <div>
                            <label style='font-weight:600;display:block;margin-bottom:4px'>Min Observations:</label>
                            <input type='number' name='min_obs' value='""" + min_obs + """' placeholder='e.g. 5' style='padding:6px;width:100%'>
                        </div>
                    </div>
                    <div style='margin-top:12px'>
                        <button type='submit' style='padding:8px 16px;background:#0b5cff;color:white;border:0;border-radius:6px;font-weight:600;cursor:pointer;margin-right:8px'>Apply Filters</button>
                        <a href='/signal-mappings' style='padding:8px 16px;background:#eee;color:#222;text-decoration:none;border-radius:6px;font-weight:600'>Clear Filters</a>
                    </div>
                </form>
            """)
            
            # Results table
            body.append(f"<h3>Signal Mappings ({len(rows)} results, max 500 shown)</h3>")
            
            # Helper function to build sort URL with current filters
            from urllib.parse import urlencode
            def sort_url(column: str) -> str:
                """Build URL for column sorting while preserving filters."""
                params = {}
                if td_area_filter:
                    params['area'] = td_area_filter
                if address_filter:
                    params['address'] = address_filter
                if from_berth_filter:
                    params['from_berth'] = from_berth_filter
                if to_berth_filter:
                    params['to_berth'] = to_berth_filter
                if min_score:
                    params['min_score'] = min_score
                if min_obs:
                    params['min_obs'] = min_obs
                params['sort'] = column
                # Toggle order if already sorting by this column
                if sort_by == column:
                    params['order'] = 'asc' if sort_order == 'desc' else 'desc'
                else:
                    # Default to desc for score/obs_count, asc for others
                    params['order'] = 'desc' if column in ['score', 'obs_count'] else 'asc'
                return f"/signal-mappings?{urlencode(params)}"
            
            def sort_indicator(column: str) -> str:
                """Return sort indicator (arrow) if this column is currently sorted."""
                if sort_by == column:
                    return " ▼" if sort_order == 'desc' else " ▲"
                return ""
            
            if rows:
                body.append("<table style='font-size:13px'>")
                body.append("<tr>")
                body.append(f"<th><a href='{sort_url('area')}' style='color:inherit;text-decoration:none'>TD Area{sort_indicator('area')}</a></th>")
                body.append(f"<th><a href='{sort_url('address')}' style='color:inherit;text-decoration:none'>Signal Address{sort_indicator('address')}</a></th>")
                body.append(f"<th><a href='{sort_url('from_berth')}' style='color:inherit;text-decoration:none'>From Berth{sort_indicator('from_berth')}</a></th>")
                body.append(f"<th><a href='{sort_url('to_berth')}' style='color:inherit;text-decoration:none'>To Berth{sort_indicator('to_berth')}</a></th>")
                body.append(f"<th><a href='{sort_url('score')}' style='color:inherit;text-decoration:none'>Score{sort_indicator('score')}</a></th>")
                body.append(f"<th><a href='{sort_url('obs_count')}' style='color:inherit;text-decoration:none'>Obs Count{sort_indicator('obs_count')}</a></th>")
                body.append(f"<th><a href='{sort_url('last_seen')}' style='color:inherit;text-decoration:none'>Last Seen{sort_indicator('last_seen')}</a></th>")
                body.append(f"<th><a href='{sort_url('location')}' style='color:inherit;text-decoration:none'>Location{sort_indicator('location')}</a></th>")
                body.append("<th>Last Data</th>")
                body.append("</tr>")
                
                for r in rows:
                    # Format score with color coding
                    score = r['score'] if r['score'] is not None else 0
                    score_color = '#22c55e' if score > 1.0 else '#f59e0b' if score > 0.5 else '#6b7280'
                    score_str = f"<span style='color:{score_color};font-weight:600'>{score:.3f}</span>"
                    
                    # Format location
                    loc = ""
                    if r['location_name']:
                        loc = r['location_name']
                        if r['stanox']:
                            loc += f" ({r['stanox']})"
                    elif r['stanox']:
                        loc = r['stanox']
                    
                    # Format timestamp
                    time_str = r['last_seen_utc'] if r['last_seen_utc'] else ""
                    if time_str:
                        # Show just the date and time part (remove milliseconds for readability)
                        time_str = time_str[:19].replace('T', ' ')
                    
                    body.append("<tr>")
                    body.append(f"<td>{r['td_area']}</td>")
                    body.append(f"<td class='mono'>{r['address']}</td>")
                    body.append(f"<td>{r['from_berth']}</td>")
                    body.append(f"<td>{r['to_berth']}</td>")
                    body.append(f"<td>{score_str}</td>")
                    body.append(f"<td>{r['obs_count']}</td>")
                    body.append(f"<td class='mono dim' style='font-size:12px'>{time_str}</td>")
                    body.append(f"<td>{loc}</td>")
                    body.append(f"<td class='dim'>{r['last_data'] if r['last_data'] else ''}</td>")
                    body.append("</tr>")
                
                body.append("</table>")
            else:
                body.append("<p><i>No mappings found matching the current filters.</i></p>")
                
        except Exception as e:
            logger.error(f"Web dashboard: Error querying signal mappings: {e}")
            body.append(f"<p style='color:red'>Error querying signal mappings: {e}</p>")
            import traceback
            body.append(f"<pre style='font-size:11px;background:#f7f9fc;padding:8px'>{traceback.format_exc()}</pre>")
        
        return render_page("Signal Mappings - NR RailHub", body, active="signal-mappings")

    # Helper functions for manual purge
    def _purge_trust_messages(conn_obj, cutoff_ms: int, batch_size: int) -> int:
        """Purge trust_messages older than cutoff_ms in batches."""
        total_deleted = 0
        while True:
            cursor = conn_obj.cursor()
            cursor.execute(
                "SELECT id FROM trust_messages WHERE created_at_ts < ? LIMIT ?",
                (cutoff_ms, batch_size)
            )
            ids = [row[0] for row in cursor.fetchall()]
            
            if not ids:
                break
            
            placeholders = ','.join('?' * len(ids))
            cursor.execute(f"DELETE FROM trust_messages WHERE id IN ({placeholders})", ids)
            conn_obj.commit()
            deleted = cursor.rowcount
            total_deleted += deleted
            
            if deleted < batch_size:
                break
        
        return total_deleted
    
    def _purge_vstp_schedules(conn_obj, cutoff_ms: int, batch_size: int) -> int:
        """Purge vstp_schedules (and locations) older than cutoff_ms in batches."""
        total_deleted = 0
        while True:
            cursor = conn_obj.cursor()
            cursor.execute(
                "SELECT uid, schedule_start_date FROM vstp_schedules WHERE created_at_ts < ? LIMIT ?",
                (cutoff_ms, batch_size)
            )
            keys = cursor.fetchall()
            
            if not keys:
                break
            
            # Delete locations first
            for uid, start_date in keys:
                cursor.execute(
                    "DELETE FROM vstp_schedule_locations WHERE uid=? AND schedule_start_date=?",
                    (uid, start_date)
                )
            
            # Delete schedule headers
            for uid, start_date in keys:
                cursor.execute(
                    "DELETE FROM vstp_schedules WHERE uid=? AND schedule_start_date=?",
                    (uid, start_date)
                )
            
            conn_obj.commit()
            deleted = len(keys)
            total_deleted += deleted
            
            if deleted < batch_size:
                break
        
        return total_deleted

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        """Settings page for configuring retention and manual purge."""
        body = ["<h2>Settings</h2>"]
        
        if request.method == "POST":
            action = request.form.get("action", "")
            
            if action == "save":
                # Save retention settings to YAML config
                config = load_yaml_config()
                
                # Get form values
                retain_trust_days = request.form.get("retain_trust_days", "").strip()
                retain_vstp_days = request.form.get("retain_vstp_days", "").strip()
                retention_interval = request.form.get("retention_interval", "").strip()
                retention_batch_size = request.form.get("retention_batch_size", "").strip()
                save_raw_json = request.form.get("save_raw_json") == "on"
                
                # Update config (convert to int if not empty)
                if retain_trust_days:
                    config["retain-trust-days"] = int(retain_trust_days)
                elif "retain-trust-days" in config:
                    del config["retain-trust-days"]
                
                if retain_vstp_days:
                    config["retain-vstp-days"] = int(retain_vstp_days)
                elif "retain-vstp-days" in config:
                    del config["retain-vstp-days"]
                
                if retention_interval:
                    config["retention-interval"] = int(retention_interval)
                
                if retention_batch_size:
                    config["retention-batch-size"] = int(retention_batch_size)
                
                # Update save-raw-json setting
                config["save-raw-json"] = save_raw_json
                
                # Save config
                if save_yaml_config(config):
                    body.append("<p style='color:green;font-weight:600'>✓ Settings saved to config file. Restart application to apply.</p>")
                else:
                    body.append("<p style='color:red;font-weight:600'>✗ Error saving config file.</p>")
            
            elif action == "purge":
                # Manual purge now
                now_ms = int(time.time() * 1000)
                
                retain_trust_days = request.form.get("retain_trust_days", "").strip()
                retain_vstp_days = request.form.get("retain_vstp_days", "").strip()
                batch_size = int(request.form.get("retention_batch_size", "1000") or 1000)
                
                deleted_trust = 0
                deleted_vstp = 0
                
                try:
                    if retain_trust_days:
                        cutoff_ms = now_ms - (int(retain_trust_days) * 24 * 60 * 60 * 1000)
                        deleted_trust = _purge_trust_messages(conn, cutoff_ms, batch_size)
                    
                    if retain_vstp_days:
                        cutoff_ms = now_ms - (int(retain_vstp_days) * 24 * 60 * 60 * 1000)
                        deleted_vstp = _purge_vstp_schedules(conn, cutoff_ms, batch_size)
                    
                    body.append(
                        f"<p style='color:green;font-weight:600'>✓ Purge completed: "
                        f"deleted {deleted_trust} trust_messages, {deleted_vstp} vstp_schedules</p>"
                    )
                except Exception as e:
                    body.append(f"<p style='color:red;font-weight:600'>✗ Purge error: {e}</p>")
        
        # Load current config
        config = load_yaml_config()
        retain_trust_days = config.get("retain-trust-days", "")
        retain_vstp_days = config.get("retain-vstp-days", "")
        retention_interval = config.get("retention-interval", 3600)
        retention_batch_size = config.get("retention-batch-size", 1000)
        save_raw_json = config.get("save-raw-json", True)
        
        # Render form
        body.append("<h3>Retention Settings</h3>")
        body.append("<p>Configure automatic data retention. Changes require application restart.</p>")
        body.append("<form method='post'>")
        body.append("<input type='hidden' name='action' value='save'>")
        body.append("<table style='width:auto'>")
        body.append("<tr><td><label for='retain_trust_days'>Retain TRUST messages (days):</label></td>")
        body.append(f"<td><input type='number' name='retain_trust_days' id='retain_trust_days' value='{retain_trust_days}' placeholder='Leave empty to disable' style='width:150px'></td></tr>")
        body.append("<tr><td><label for='retain_vstp_days'>Retain VSTP schedules (days):</label></td>")
        body.append(f"<td><input type='number' name='retain_vstp_days' id='retain_vstp_days' value='{retain_vstp_days}' placeholder='Leave empty to disable' style='width:150px'></td></tr>")
        body.append("<tr><td><label for='retention_interval'>Retention check interval (seconds):</label></td>")
        body.append(f"<td><input type='number' name='retention_interval' id='retention_interval' value='{retention_interval}' style='width:150px'></td></tr>")
        body.append("<tr><td><label for='retention_batch_size'>Retention batch size:</label></td>")
        body.append(f"<td><input type='number' name='retention_batch_size' id='retention_batch_size' value='{retention_batch_size}' style='width:150px'></td></tr>")
        body.append("<tr><td><label for='save_raw_json'>Save raw JSON messages:</label></td>")
        body.append(f"<td><input type='checkbox' name='save_raw_json' id='save_raw_json' {'checked' if save_raw_json else ''}> <span style='color:#666;font-size:0.9em'>(Disabling reduces database size but loses original message data)</span></td></tr>")
        body.append("<tr><td colspan='2' style='padding-top:12px'><button type='submit' style='padding:10px 20px;background:#0b5cff;color:white;border:0;border-radius:6px;cursor:pointer'>Save Settings</button></td></tr>")
        body.append("</table>")
        body.append("</form>")
        
        body.append("<h3>Manual Purge</h3>")
        body.append("<p>Run purge immediately with current settings (does not require restart).</p>")
        body.append("<form method='post'>")
        body.append("<input type='hidden' name='action' value='purge'>")
        body.append(f"<input type='hidden' name='retain_trust_days' value='{retain_trust_days}'>")
        body.append(f"<input type='hidden' name='retain_vstp_days' value='{retain_vstp_days}'>")
        body.append(f"<input type='hidden' name='retention_batch_size' value='{retention_batch_size}'>")
        body.append("<button type='submit' style='padding:10px 20px;background:#f59e0b;color:white;border:0;border-radius:6px;cursor:pointer'>Run Purge Now</button>")
        body.append("</form>")
        
        return render_page("Settings - NR RailHub", body, active="config")

    # Configure werkzeug logging to suppress console output in interactive mode
    if log_queue is not None:
        # Suppress Flask's default logging to console
        import logging
        from .curses_view import QueueHandler
        
        # Disable werkzeug's default console logging
        werkzeug_logger = logging.getLogger('werkzeug')
        werkzeug_logger.handlers.clear()  # Remove default handlers
        werkzeug_logger.propagate = False
        
        # Add queue handler for HTTP request logging
        queue_handler = QueueHandler(log_queue)
        queue_handler.setLevel(logging.INFO)
        queue_handler.setFormatter(logging.Formatter('[HTTP] %(message)s'))
        werkzeug_logger.addHandler(queue_handler)
        werkzeug_logger.setLevel(logging.INFO)
        
        # Also suppress Flask's app logger
        flask_app_logger = logging.getLogger('flask.app')
        flask_app_logger.handlers.clear()
        flask_app_logger.propagate = False
        
        logger.info(f"Starting web dashboard on http://0.0.0.0:{port} (log_queue mode)")
    else:
        logger.info(f"Starting web dashboard on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

