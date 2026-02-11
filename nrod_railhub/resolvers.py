#!/usr/bin/env python3
"""Location and data resolvers for nrod_railhub."""

from __future__ import annotations

import base64
import gzip
import io
import json
import os
import pathlib
import urllib.request
import urllib.parse
from urllib.error import URLError, HTTPError
from typing import Any, Dict, List, Optional, Tuple

from .models import CORPUS_URL, SMART_URL, utc_now_iso, hhmmss_to_hhmm
from .logging_config import get_logger

logger = get_logger("resolvers")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stop urllib auto-following redirects so we can manage headers safely."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LocationResolver:
    """
    Loads Network Rail CORPUSExtract-style JSON and provides:
      - TIPLOC -> name
      - STANOX -> name
      - CRS (3-alpha) -> name

    CORPUS is documented as mapping STANOX/TIPLOC/NLC/UIC/CRS to location descriptions.
    """

    def __init__(self) -> None:
        self.tiploc_to_name: Dict[str, str] = {}
        self.stanox_to_name: Dict[str, str] = {}
        self.crs_to_name: Dict[str, str] = {}
        self.tiploc_to_stanox: Dict[str, str] = {}  # TIPLOC -> STANOX mapping

    def load_or_download(
        self,
        username: str,
        password: str,
        cache_path: str,
        force: bool = False,
        quiet: bool = False,
    ) -> None:
        path = pathlib.Path(cache_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

        if force or (not path.exists()):
            if not quiet:
                logger.info(f"CORPUS: downloading to {path} ...")
            self._download_corpus(username, password, str(path))
        else:
            if not quiet:
                logger.info(f"CORPUS: using cached file {path}")

        self._load_corpus_file(str(path), quiet=quiet)

    def _download_corpus(self, username: str, password: str, out_file: str) -> None:
        """
        Download CORPUS via Network Rail SupportingFileAuthenticate.

        Flow:
          1) GET CORPUS_URL with Basic Auth to publicdatafeeds.networkrail.co.uk
          2) Follow redirect(s) to AWS pre-signed URL(s) WITHOUT Authorization header
             (S3 rejects Authorization when using X-Amz-* pre-signed query auth)
          3) Transparently decompress gzip if needed
          4) Save decompressed JSON to out_file
        """

        def is_presigned_aws(url: str) -> bool:
            q = urllib.parse.urlparse(url).query
            return "X-Amz-Algorithm=" in q or "X-Amz-Signature=" in q

        def host_of(url: str) -> str:
            return (urllib.parse.urlparse(url).hostname or "").lower()

        nr_host = "publicdatafeeds.networkrail.co.uk"
        auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")

        url = CORPUS_URL
        raw = b""
        encoding = ""
        ctype = ""

        for hop in range(0, 8):  # follow up to 8 redirects
            h = host_of(url)
            use_auth = (h == nr_host) and (not is_presigned_aws(url))

            headers = {
                "User-Agent": "vstp_trust_td_human/1.0 (+python urllib)",
                "Accept": "*/*",
                "Accept-Encoding": "gzip",
            }
            if use_auth:
                headers["Authorization"] = f"Basic {auth}"

            req = urllib.request.Request(url, headers=headers)
            try:
                # Prevent urllib from auto-following redirects so we can control headers per-hop
                opener = urllib.request.build_opener(NoRedirect())
                resp = opener.open(req, timeout=60)

                # Success (2xx)
                raw = resp.read()
                encoding = (resp.headers.get("Content-Encoding") or "").lower().strip()
                ctype = (resp.headers.get("Content-Type") or "").lower().strip()
                break

            except HTTPError as e:
                # Redirects come as HTTPError when NoRedirect is used
                if e.code in (301, 302, 303, 307, 308):
                    loc = e.headers.get("Location") or e.headers.get("location")
                    if not loc:
                        raise RuntimeError(f"CORPUS redirect ({e.code}) without Location header") from e
                    # Handle relative redirects
                    url = urllib.parse.urljoin(url, loc)
                    continue

                # Non-redirect error
                snippet = ""
                try:
                    snippet = e.read(500).decode("utf-8", errors="replace")
                except Exception:
                    pass  # Ignore if we can't read error body
                raise RuntimeError(f"CORPUS download HTTP error: {e.code} {e.reason} body={snippet!r}") from e

            except URLError as e:
                raise RuntimeError(f"CORPUS download failed: {e}") from e
        else:
            raise RuntimeError("CORPUS download failed: too many redirects")

        # gzip detection: header OR magic bytes OR content-type hint
        is_gzip = encoding == "gzip" or raw[:2] == b"\x1f\x8b" or "gzip" in ctype
        if is_gzip:
            try:
                raw = gzip.decompress(raw)
            except Exception:
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gf:
                    raw = gf.read()

        tmp = out_file + ".tmp"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, out_file)


    def _load_corpus_file(self, filename: str, quiet: bool = False) -> None:
        with open(filename, "rb") as f:
            raw = f.read()

        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            
            # Handle double-encoded JSON: if result is a string, try parsing again
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                    if not quiet:
                        logger.debug("CORPUS: detected and handled double-encoded JSON")
                except (json.JSONDecodeError, TypeError):
                    # If second parse fails, continue with the string (not double-encoded)
                    pass
                    
        except Exception as e:
            raise RuntimeError(f"CORPUS parse failed (not valid JSON?): {e}") from e

        # CORPUS can be either:
        #   - a list of dict rows
        #   - a wrapper dict, commonly {"TIPLOCDATA": [ ... ]}
        rows: Optional[List[Any]] = None

        if isinstance(payload, list):
            rows = payload
            if not quiet:
                logger.debug("CORPUS format: list")
        elif isinstance(payload, dict):
            if not quiet:
                logger.debug(f"CORPUS format: dict wrapper keys={list(payload.keys())}")

            # Common wrapper keys
            for key in ("TIPLOCDATA", "tiplocdata", "locations", "data"):
                v = payload.get(key)
                if isinstance(v, list):
                    rows = v
                    break

            # Single-key dict whose value is the list
            if rows is None and len(payload) == 1:
                v = next(iter(payload.values()))
                if isinstance(v, list):
                    rows = v

        if rows is None:
            raise RuntimeError(
                f"CORPUS unexpected format: expected list or wrapper dict containing a list; "
                f"got {type(payload)}"
            )

        tiploc: Dict[str, str] = {}
        stanox: Dict[str, str] = {}
        crs: Dict[str, str] = {}
        tiploc_stanox: Dict[str, str] = {}

        for row in rows:
            if not isinstance(row, dict):
                continue

            # Fields present in CORPUSExtract rows often include:
            # TIPLOC, STANOX, 3ALPHA, NLCDESC (sometimes NLCDESC16)
            name = (row.get("NLCDESC") or row.get("NLCDESC16") or "").strip()
            if not name:
                continue

            tip = (row.get("TIPLOC") or "").strip().upper()
            stx = (row.get("STANOX") or "").strip()
            three = (row.get("3ALPHA") or "").strip().upper()

            if tip and tip not in tiploc:
                tiploc[tip] = name
            if stx and stx not in stanox:
                stanox[stx] = name
            if three and three not in crs:
                crs[three] = name
            # Build TIPLOC -> STANOX mapping
            if tip and stx and tip not in tiploc_stanox:
                tiploc_stanox[tip] = stx

        self.tiploc_to_name = tiploc
        self.stanox_to_name = stanox
        self.crs_to_name = crs
        self.tiploc_to_stanox = tiploc_stanox

        if not quiet:
            logger.info(
                f"CORPUS loaded: "
                f"{len(tiploc)} TIPLOC, {len(stanox)} STANOX, {len(crs)} CRS mappings"
            )



    def name_for_tiploc(self, code: str) -> str:
        return self.tiploc_to_name.get((code or "").strip().upper(), "")

    def name_for_stanox(self, code: str) -> str:
        return self.stanox_to_name.get((code or "").strip(), "")

    def name_for_crs(self, code: str) -> str:
        return self.crs_to_name.get((code or "").strip().upper(), "")

    def stanox_for_tiploc(self, code: str) -> Optional[str]:
        """Return STANOX for a given TIPLOC, or None if not found."""
        return self.tiploc_to_stanox.get((code or "").strip().upper())
    
    def add_tiploc_data(self, tiploc_records: List[Dict[str, Any]], quiet: bool = False) -> int:
        """Add TIPLOC data from schedule files to the resolver.
        
        This allows enriching the location resolver with TIPLOC data extracted
        from CIF schedule files, which may contain locations not in CORPUS.
        
        Args:
            tiploc_records: List of TIPLOC records with keys: tiploc, name, stanox, crs
            quiet: If True, suppress log messages
            
        Returns:
            Number of new TIPLOC entries added (not counting duplicates)
        """
        added = 0
        
        for record in tiploc_records:
            tiploc = record.get("tiploc", "").strip().upper()
            if not tiploc:
                continue
            
            name = record.get("name", "").strip()
            stanox = record.get("stanox", "").strip()
            crs = record.get("crs", "").strip().upper()
            
            # Add TIPLOC -> name mapping if we have a name and it's new
            if name and tiploc not in self.tiploc_to_name:
                self.tiploc_to_name[tiploc] = name
                added += 1
            
            # Add STANOX -> name mapping if we have both
            if stanox and name and stanox not in self.stanox_to_name:
                self.stanox_to_name[stanox] = name
            
            # Add CRS -> name mapping if we have both
            if crs and name and crs not in self.crs_to_name:
                self.crs_to_name[crs] = name
            
            # Add TIPLOC -> STANOX mapping
            if tiploc and stanox and tiploc not in self.tiploc_to_stanox:
                self.tiploc_to_stanox[tiploc] = stanox
        
        if not quiet and added > 0:
            logger.info(f"Added {added} new TIPLOC entries from schedule data")
        
        return added


class SmartResolver:
    """Loads SMART berth stepping reference data and provides TD+berth -> STANOX/platform/name.
    
    Supports fallback to inferred berth-signal data from database when SMART data is unavailable.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        # Keyed by (td_area, berth) e.g. ("AD","0152") -> dict with stanox/platform/stanme
        self.berth_map: Dict[Tuple[str, str], Dict[str, str]] = {}
        self.db_path = db_path
        self._db_conn = None
        
        # Initialize database connection if path provided
        # Note: check_same_thread=False is used because SmartResolver lookup may be called
        # from different threads (e.g., STOMP receiver thread). The connection is read-only
        # and SQLite supports concurrent reads safely.
        if db_path:
            import sqlite3
            import logging
            logger = logging.getLogger(__name__)
            try:
                self._db_conn = sqlite3.connect(db_path, check_same_thread=False, timeout=5.0)
                self._db_conn.row_factory = sqlite3.Row
            except Exception as e:
                logger.warning(f"SmartResolver: Failed to connect to database {db_path}: {e}")
                self._db_conn = None  # Silently fail; fallback won't work but SMART will

    def load_or_download(
        self,
        username: str,
        password: str,
        cache_path: str,
        force: bool = False,
        quiet: bool = False,
    ) -> None:
        path = pathlib.Path(cache_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

        if force or (not path.exists()):
            if not quiet:
                logger.info(f"SMART: downloading to {path} ...")
            # Re-use LocationResolver's downloader (same auth + redirect rules)
            lr = LocationResolver()
            lr._download_corpus(username, password, str(path).replace("CORPUS", "SMART"))  # legacy fallback
            # The above line isn't reliable if path doesn't contain CORPUS; do it properly:
            self._download_smart(username, password, str(path))
        else:
            if not quiet:
                logger.info(f"SMART: using cached file {path}")

        self._load_smart_file(str(path), quiet=quiet)

    def _download_smart(self, username: str, password: str, out_file: str) -> None:
        """Download SMART via SupportingFileAuthenticate (same redirect/auth rules as CORPUS)."""

        def is_presigned_aws(url: str) -> bool:
            q = urllib.parse.urlparse(url).query
            return "X-Amz-Algorithm=" in q or "X-Amz-Signature=" in q

        def host_of(url: str) -> str:
            return (urllib.parse.urlparse(url).hostname or "").lower()

        nr_host = "publicdatafeeds.networkrail.co.uk"
        auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")

        url = SMART_URL
        raw = b""
        encoding = ""
        ctype = ""

        for hop in range(0, 8):
            h = host_of(url)
            use_auth = (h == nr_host) and (not is_presigned_aws(url))

            headers = {
                "User-Agent": "vstp_trust_td_human/1.1 (+python urllib)",
                "Accept": "*/*",
                "Accept-Encoding": "gzip",
            }
            if use_auth:
                headers["Authorization"] = f"Basic {auth}"

            req = urllib.request.Request(url, headers=headers)
            try:
                opener = urllib.request.build_opener(NoRedirect())
                resp = opener.open(req, timeout=60)
                raw = resp.read()
                encoding = (resp.headers.get("Content-Encoding") or "").lower().strip()
                ctype = (resp.headers.get("Content-Type") or "").lower().strip()
                break
            except HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    loc = e.headers.get("Location") or e.headers.get("location")
                    if not loc:
                        raise RuntimeError(f"SMART redirect ({e.code}) without Location header") from e
                    url = urllib.parse.urljoin(url, loc)
                    continue
                snippet = ""
                try:
                    snippet = e.read(500).decode("utf-8", errors="replace")
                except Exception:
                    pass  # Ignore if we can't read error body
                raise RuntimeError(f"SMART download HTTP error: {e.code} {e.reason} body={snippet!r}") from e
            except URLError as e:
                raise RuntimeError(f"SMART download failed: {e}") from e
        else:
            raise RuntimeError("SMART download failed: too many redirects")

        is_gzip = encoding == "gzip" or raw[:2] == b"\x1f\x8b" or "gzip" in ctype
        if is_gzip:
            try:
                raw = gzip.decompress(raw)
            except Exception:
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gf:
                    raw = gf.read()

        tmp = out_file + ".tmp"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, out_file)

    def _load_smart_file(self, filename: str, quiet: bool = False) -> None:
        with open(filename, "rb") as f:
            raw = f.read()

        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            
            # Handle double-encoded JSON: if result is a string, try parsing again
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                    if not quiet:
                        logger.debug("SMART: detected and handled double-encoded JSON")
                except (json.JSONDecodeError, TypeError):
                    # If second parse fails, continue with the string (not double-encoded)
                    pass
                    
        except Exception as e:
            raise RuntimeError(f"SMART parse failed (not valid JSON?): {e}") from e

        rows: Optional[List[Any]] = None
        if isinstance(payload, list):
            rows = payload
            if not quiet:
                logger.debug("SMART format: list")
        elif isinstance(payload, dict):
            if not quiet:
                logger.debug(f"SMART format: dict wrapper keys={list(payload.keys())}")
            for key in ("SMARTDATA", "smartdata", "data", "rows", "SMART", "BERTHDATA"):
                v = payload.get(key)
                if isinstance(v, list):
                    rows = v
                    break
            if rows is None and len(payload) == 1:
                v = next(iter(payload.values()))
                if isinstance(v, list):
                    rows = v

        if rows is None:
            raise RuntimeError(
                f"SMART unexpected format: expected list or wrapper dict containing a list; got {type(payload)}"
            )

        mp: Dict[Tuple[str, str], Dict[str, str]] = {}

        for row in rows:
            if not isinstance(row, dict):
                continue
            td = (row.get("TD") or "").strip().upper()
            stanox = (row.get("STANOX") or "").strip()
            if not td or not stanox:
                continue

            platform = (row.get("PLATFORM") or "").strip()
            stanme = (row.get("STANME") or "").strip()
            event = (row.get("EVENT") or "").strip().upper()

            for berth_key in ("TOBERTH", "FROMBERTH"):
                berth = (row.get(berth_key) or "").strip().upper()
                if not berth:
                    continue
                k = (td, berth)
                if k not in mp:
                    mp[k] = {
                        "stanox": stanox,
                        "platform": platform,
                        "stanme": stanme,
                        "event": event,
                    }

        self.berth_map = mp
        if not quiet:
            logger.info(f"SMART loaded: {len(self.berth_map)} berth mappings")

    def lookup(self, td_area: str, berth: str) -> Optional[Dict[str, str]]:
        """Look up berth information, with fallback to inferred data.
        
        First tries SMART data (from JSON file), then falls back to querying
        the berth_signal_scores table if a database connection is available.
        
        Args:
            td_area: 2-character TD area code (e.g. "EK", "AD")
            berth: Berth identifier (e.g. "0152")
            
        Returns:
            Dict with keys: stanox, platform, stanme, event (from SMART)
            OR dict with keys: stanox, confidence (from inferred data)
            OR None if not found in either source
        """
        k = ((td_area or "").strip().upper(), (berth or "").strip().upper())
        
        # Try SMART first (in-memory lookup)
        result = self.berth_map.get(k)
        if result:
            return result
        
        # Fallback to database inferred berth data
        if self._db_conn:
            return self._lookup_inferred_berth(k[0], k[1])
        
        return None
    
    def _lookup_inferred_berth(self, td_area: str, berth: str) -> Optional[Dict[str, str]]:
        """Query database for inferred berth-to-signal mapping.
        
        Uses berth_signal_scores table populated by the mapper from historical TD data.
        Returns the highest-scoring (most confident) STANOX for this berth.
        
        Args:
            td_area: TD area code (normalized)
            berth: Berth identifier (normalized)
            
        Returns:
            Dict with stanox and confidence score, or None if not found
        """
        if not self._db_conn:
            return None
        
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            cursor = self._db_conn.cursor()
            
            # Query berth_signal_scores for best match
            # We look for berths appearing in either from_berth or to_berth position
            # and pick the highest-scoring association
            cursor.execute("""
                SELECT
                    bss.td_area,
                    COALESCE(bss.from_berth, bss.to_berth) as berth,
                    ct.stanox,
                    ct.nlcdesc as location_name,
                    bss.score,
                    bss.obs_count
                FROM berth_signal_scores bss
                LEFT JOIN corpus_tiploc ct ON CAST(bss.address AS TEXT) = CAST(ct.stanox AS TEXT)
                WHERE bss.td_area = ?
                  AND (bss.from_berth = ? OR bss.to_berth = ?)
                ORDER BY bss.score DESC
                LIMIT 1
            """, (td_area, berth, berth))
            
            row = cursor.fetchone()
            if row and row['stanox']:
                return {
                    "stanox": str(row['stanox']),
                    "stanme": row['location_name'] or "",
                    "platform": "",  # Not available in inferred data
                    "event": "INFERRED",  # Mark as inferred to distinguish from SMART
                    "confidence": float(row['score']) if row['score'] else 0.0,
                }
        except Exception as e:
            # Log database errors but don't crash the resolver
            logger.debug(f"SmartResolver: Failed to query inferred berth for {td_area}:{berth}: {e}")
        
        return None


class TdAreaResolver:
    """Maps TD area codes to human-readable names.
    
    Source: https://wiki.openraildata.com/index.php/List_of_Train_Describers
    TD areas are 2-character signalling control area codes used in the TD feed.
    """
    
    # Official TD area code to name mappings from Network Rail Open Data Wiki
    TD_AREA_NAMES: Dict[str, str] = {
        "AD": "Ashford",
        "AG": "Aberdeen",
        "AJ": "Aberdeen Junction",
        "AM": "Acton Main Line",
        "AN": "Anglia",
        "AY": "Aylesbury",
        "BG": "Birmingham",
        "BM": "Birmingham New Street",
        "BN": "Brighton",
        "BP": "Bristol Panel",
        "BR": "Bristol",
        "BS": "Bristol Parkway",
        "BT": "Bletchley",
        "BW": "Bescot Yard",
        "BX": "Basingstoke",
        "CA": "Cambridge",
        "CB": "Carlisle",
        "CC": "Cardiff Canton",
        "CD": "Crewe",
        "CE": "Chesterfield",
        "CF": "Cardiff",
        "CG": "Cambridge",
        "CH": "Charing Cross",
        "CL": "Colchester",
        "CR": "Crewe",
        "CS": "Cricklewood",
        "CT": "Canterbury",
        "CW": "Crown Point",
        "CY": "Croydon",
        "DB": "Derby",
        "DD": "Didcot",
        "DE": "Derby",
        "DF": "Doncaster",
        "DG": "Dungeness",
        "DN": "Doncaster",
        "DO": "Doncaster",
        "DR": "Doncaster",
        "DU": "Dundee",
        "DY": "Derby",
        "EA": "East Anglia",
        "EB": "Edinburgh",
        "EC": "Edinburgh",
        "ED": "Edinburgh",
        "EK": "East Kent",  # Gillingham area
        "EL": "Ely",
        "EM": "East Midlands",
        "EN": "Enfield",
        "EP": "Euston Power Box",
        "ER": "Eastleigh",  # Note: ER is Eastleigh, not EK
        "EX": "Exeter",
        "EY": "Ely",
        "FA": "Faversham",
        "FD": "Farringdon",
        "FE": "Feltham",
        "FF": "Fenchurch Street",
        "FG": "Folkestone",
        "FH": "Finsbury Park",
        "FN": "Finsbury Park",
        "FP": "Finsbury Park",
        "FR": "Ferme Park",
        "FY": "Fenny Stratford",
        "GB": "Glasgow",
        "GD": "Guildford",
        "GE": "Gillingham",
        "GF": "Gillingham",
        "GG": "Glasgow",
        "GL": "Gloucester",
        "GM": "Gillingham",
        "GN": "Grantham",
        "GP": "Gospel Oak",
        "GR": "Grantham",
        "GS": "Glasgow South",
        "GT": "Gatwick",
        "GW": "Gloucester",
        "GY": "Gateshead",
        "HB": "Hornsey",
        "HD": "Haywards Heath",
        "HE": "Hereford",
        "HF": "Hatfield",
        "HG": "Huntingdon",
        "HN": "Hornsey",
        "HP": "Harpenden",
        "HR": "Harrow",
        "HT": "Hitchin",
        "HW": "Heaton",
        "HX": "Hexham",
        "HY": "Haywards Heath",
        "IF": "Ilford",
        "IM": "Ipswich",
        "IP": "Ipswich",
        "KC": "Kings Cross",
        "KE": "Kentish Town",
        "KL": "Kilmarnock",
        "KN": "Kentish Town",
        "KT": "Kings Norton",
        "KX": "Kings Cross",
        "LA": "Lancaster",
        "LB": "London Bridge",
        "LC": "Leicester",
        "LD": "Leeds",
        "LE": "Leicester",
        "LG": "Lincoln",
        "LI": "Liverpool",
        "LM": "Liverpool Street Moorgate",
        "LN": "Lincoln",
        "LO": "London",
        "LP": "Liverpool Lime Street",
        "LR": "Leicester",
        "LS": "Liverpool Street",
        "LT": "Luton",
        "LV": "Liverpool",
        "LY": "Leyland",
        "MA": "Manchester",
        "MB": "Marylebone",
        "MC": "Manchester",
        "MD": "Maidstone",
        "ME": "Motherwell",
        "MG": "Margam",
        "MH": "Motherwell",
        "MK": "Milton Keynes",
        "ML": "Motherwell",
        "MM": "Manchester",
        "MN": "Manchester",
        "MO": "Manchester",
        "MR": "Manchester",
        "MS": "Manchester South",
        "MT": "Margate",
        "MW": "Motherwell",
        "MY": "Morley",
        "NC": "Newcastle",
        "ND": "North Dulwich",
        "NE": "Newcastle",
        "NL": "New Barnet",
        "NM": "Normanton",
        "NN": "Norwich",
        "NO": "Nottingham",
        "NR": "Norwich",
        "NT": "Nottingham",
        "NW": "Newport",
        "NY": "New Malden",
        "OR": "Orpington",
        "OX": "Oxford",
        "PA": "Paddington",
        "PB": "Peterborough",
        "PC": "Preston",
        "PD": "Paddington",
        "PE": "Perth",
        "PG": "Preston",
        "PH": "Portsmouth Harbour",
        "PL": "Plymouth",
        "PM": "Peterborough",
        "PN": "Preston",
        "PP": "Portsmouth",
        "PR": "Preston",
        "PS": "Paisley",
        "PT": "Perth",
        "PY": "Plymouth",
        "RA": "Ramsgate",
        "RD": "Reading",
        "RE": "Reading",
        "RF": "Redhill",
        "RG": "Reading",
        "RL": "Rotherham",
        "RM": "Romford",
        "RO": "Rochester",
        "RP": "Redditch",
        "RY": "Rugby",
        "SA": "South Anglia",
        "SB": "Salisbury",
        "SC": "Stafford",
        "SD": "Sunderland",
        "SE": "Selhurst",
        "SF": "Stafford",
        "SG": "Stirling",
        "SH": "Sheffield",
        "SI": "Sittingbourne",
        "SL": "Slough",
        "SM": "Sunderland",
        "SN": "Swindon",
        "SO": "Southampton",
        "SP": "St Pancras",
        "SR": "Shrewsbury",
        "SS": "Stoke on Trent",
        "ST": "Stratford",
        "SU": "Sunderland",
        "SW": "Swindon",
        "SY": "Stirling",
        "TB": "Tyneside",
        "TD": "Thornaby",
        "TH": "Three Bridges",
        "TN": "Tonbridge",
        "TO": "Tyne & Wear",
        "TR": "Trent",
        "TT": "Totton",
        "TW": "Trowbridge",
        "TY": "Tyseley",
        "VI": "Victoria",
        "VX": "Vauxhall",
        "WA": "Warrington",
        "WB": "Willesden",
        "WC": "Waterloo",
        "WD": "Wembley",
        "WE": "Westbury",
        "WF": "Watford",
        "WG": "Wigan",
        "WH": "Whitehall",
        "WI": "Willesden",
        "WJ": "Willesden Junction",
        "WK": "Wakefield",
        "WL": "Waterloo",
        "WM": "Wolverhampton",
        "WN": "Waterloo",
        "WO": "Wolverhampton",
        "WR": "Warrington",
        "WS": "Westbury",
        "WT": "Watford",
        "WV": "Wolverhampton",
        "WW": "West Hampstead",
        "WX": "Wolverhampton",
        "WY": "Wembley",
        "YK": "York",
        "YO": "York",
        "YR": "York",
    }
    
    @classmethod
    def name_for_td_area(cls, code: str) -> str:
        """Get human-readable name for TD area code.
        
        Args:
            code: 2-character TD area code (e.g. "EK", "AD")
            
        Returns:
            Human-readable name, or empty string if not found
        """
        return cls.TD_AREA_NAMES.get((code or "").strip().upper(), "")


class ScheduleResolver:
    """Downloads and reads the daily SCHEDULE (ITPS) extract (JSON-in-gzip).

    This is the feed that contains the planned timetable, as distinct from VSTP
    (late-notice changes). The JSON file is line-oriented: one JSON record per line.
    """

    AUTH_URL = "https://publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate"

    def __init__(self) -> None:
        pass

    def download(
        self,
        username: str,
        password: str,
        out_gz: str,
        schedule_type: str = "CIF_ALL_FULL_DAILY",
        day: str = "toc-full",
        quiet: bool = False,
    ) -> None:
        """Download the schedule gzip to out_gz (kept compressed)."""

        url = f"{self.AUTH_URL}?type={urllib.parse.quote(schedule_type)}&day={urllib.parse.quote(day)}"

        # Basic Auth for the authenticate endpoint.
        # NOTE: urllib will otherwise *auto-follow* 302 redirects and (depending on version)
        # can carry over headers like Authorization to the S3 URL, which can result in 400.
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):  # type: ignore[override]
                return None

        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Basic {token}",
                "User-Agent": "nrod-schedule-client/1",
            },
        )

        try:
            resp = opener.open(req, timeout=60)
            # If no redirect happened (unexpected but possible), just read it.
            data = resp.read()
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308):
                raise
            loc = e.headers.get("Location") or e.headers.get("location")
            if not loc:
                raise RuntimeError("SCHEDULE: redirect without Location header")
            if not quiet:
                logger.debug(f"SCHEDULE: redirect to {loc[:90]}...")
            # Follow redirect to pre-signed S3 URL without Authorization header.
            req2 = urllib.request.Request(loc, headers={"User-Agent": "nrod-schedule-client/1"})
            with urllib.request.urlopen(req2, timeout=180) as resp2:
                data = resp2.read()

        os.makedirs(os.path.dirname(out_gz) or ".", exist_ok=True)
        tmp = out_gz + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, out_gz)

    def download_toc_schedule(
        self,
        username: str,
        password: str,
        toc_code: str,
        business_code: str,
        out_gz: str,
        update_mode: bool = False,
        day: str = "toc-full",
        quiet: bool = False,
    ) -> None:
        """Download a TOC-specific schedule file.
        
        The schedule type format CIF_XX_TOC_FULL_DAILY uses 2-letter business codes (e.g., HU, HY, HW).
        Returns JSON format despite the "CIF" prefix.
        Only CIF_ALL_FULL_DAILY with .CIF.gz suffix returns actual CIF format.
        
        Args:
            username: Network Rail username
            password: Network Rail password
            toc_code: 2-character TOC code (e.g., 'SE', 'GW')
            business_code: 2-letter business code for the TOC (e.g., 'HU' for Southeastern)
            out_gz: Path to save the downloaded gzip file
            update_mode: If True, downloads UPDATE_DAILY, otherwise FULL_DAILY
            day: Day selector for the schedule (e.g., 'toc-full' or 'toc-update-mon')
            quiet: If True, suppress log messages
        """
        schedule_type = f"CIF_{business_code}_TOC_UPDATE_DAILY" if update_mode else f"CIF_{business_code}_TOC_FULL_DAILY"
        
        if not quiet:
            mode_str = "update" if update_mode else "full"
            logger.info(f"Downloading {mode_str} schedule for {toc_code} (business code: {business_code})...")
        
        # Use the existing download method with TOC-specific parameters
        self.download(
            username=username,
            password=password,
            out_gz=out_gz,
            schedule_type=schedule_type,
            day=day,
            quiet=quiet,
        )

    def download_multiple_toc_schedules(
        self,
        username: str,
        password: str,
        toc_filter: List[str],
        toc_resolver: 'TOCResolver',
        cache_dir: str,
        update_mode: bool = False,
        day: str = "toc-full",
        quiet: bool = False,
    ) -> List[Tuple[str, str]]:
        """Download schedules for multiple TOCs based on filter.
        
        Args:
            username: Network Rail username
            password: Network Rail password
            toc_filter: List of 2-character TOC codes to download
            toc_resolver: TOCResolver instance to get business codes
            cache_dir: Directory to store downloaded files
            update_mode: If True, downloads UPDATE_DAILY, otherwise FULL_DAILY
            day: Day selector for the schedule
            quiet: If True, suppress log messages
            
        Returns:
            List of (toc_code, file_path) tuples for successfully downloaded files
        """
        downloaded_files = []
        
        for toc_code in toc_filter:
            # Get business code for this TOC
            toc_data = toc_resolver.TOC_DATA.get(toc_code.upper())
            if not toc_data:
                if not quiet:
                    logger.warning(f"TOC code {toc_code} not found in TOC reference data, skipping")
                continue
                
            business_code = toc_data.get('business_code')
            if not business_code:
                if not quiet:
                    logger.warning(f"No business code for TOC {toc_code}, skipping")
                continue
            
            # Construct output path
            mode_suffix = "_update" if update_mode else "_full"
            out_gz = os.path.join(cache_dir, f"schedule_{toc_code.upper()}{mode_suffix}.json.gz")
            
            try:
                self.download_toc_schedule(
                    username=username,
                    password=password,
                    toc_code=toc_code.upper(),
                    business_code=business_code,
                    out_gz=out_gz,
                    update_mode=update_mode,
                    day=day,
                    quiet=quiet,
                )
                downloaded_files.append((toc_code.upper(), out_gz))
                
                if not quiet:
                    file_size = os.path.getsize(out_gz) / (1024 * 1024)  # MB
                    logger.info(f"Downloaded {toc_code} schedule ({file_size:.1f}MB)")
                    
            except Exception as e:
                if not quiet:
                    logger.error(f"Failed to download schedule for {toc_code}: {e}")
                # Continue with other TOCs even if one fails
                continue
        
        return downloaded_files

    def extract_tiploc_data(
        self,
        gz_path: str,
        quiet: bool = False,
    ) -> List[Dict[str, str]]:
        """Extract TIPLOC data from the beginning of a CIF schedule file.
        
        According to the OpenRailData documentation, TOC-specific schedule files
        contain TIPLOC reference data in the first few thousand lines before the
        actual schedule records.
        
        Args:
            gz_path: Path to the gzipped schedule file
            quiet: If True, suppress log messages
            
        Returns:
            List of TIPLOC records as dictionaries with keys:
            - tiploc: TIPLOC code
            - name: Station/location name (if available)
            - stanox: STANOX code (if available)
            - crs: CRS code (if available)
        """
        import gzip
        import json
        
        tiploc_records = []
        path = pathlib.Path(gz_path).expanduser()
        
        if not path.exists():
            if not quiet:
                logger.warning(f"Schedule file not found: {gz_path}")
            return tiploc_records
        
        try:
            with gzip.open(str(path), "rt", encoding="utf-8", errors="replace") as f:
                # According to docs, TIPLOC data is in the first few thousand lines
                # We'll scan the first 10,000 lines or until we see schedule records
                line_count = 0
                max_lines_to_scan = 10000
                
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    line_count += 1
                    if line_count > max_lines_to_scan:
                        break
                    
                    try:
                        obj = json.loads(line)
                        
                        # Look for TIPLOC records (various possible formats)
                        # Format 1: {"TiplocV1": {...}}
                        if "TiplocV1" in obj:
                            tiploc_data = obj["TiplocV1"]
                            tiploc_code = tiploc_data.get("tiploc_code", "").strip()
                            if tiploc_code:
                                record = {
                                    "tiploc": tiploc_code,
                                    "name": tiploc_data.get("nlc_description", "").strip() or tiploc_data.get("tps_description", "").strip(),
                                    "stanox": tiploc_data.get("stanox", "").strip(),
                                    "crs": tiploc_data.get("three_alpha", "").strip() or tiploc_data.get("crs_code", "").strip(),
                                }
                                tiploc_records.append(record)
                        
                        # Format 2: Direct TIPLOC data (alternative format)
                        elif "tiploc_code" in obj:
                            tiploc_code = obj.get("tiploc_code", "").strip()
                            if tiploc_code:
                                record = {
                                    "tiploc": tiploc_code,
                                    "name": obj.get("nlc_description", "").strip() or obj.get("tps_description", "").strip(),
                                    "stanox": obj.get("stanox", "").strip(),
                                    "crs": obj.get("three_alpha", "").strip() or obj.get("crs_code", "").strip(),
                                }
                                tiploc_records.append(record)
                        
                        # If we see a schedule record, we've passed the TIPLOC section
                        elif "JsonScheduleV1" in obj:
                            # This marks the start of actual schedule data
                            break
                            
                    except json.JSONDecodeError:
                        # Skip invalid JSON lines
                        continue
            
            if not quiet and tiploc_records:
                logger.info(f"Extracted {len(tiploc_records)} TIPLOC records from {gz_path}")
                
        except Exception as e:
            if not quiet:
                logger.error(f"Failed to extract TIPLOC data from {gz_path}: {e}")
        
        return tiploc_records


class TOCResolver:
    """
    Resolves TOC (Train Operating Company) codes to human-readable names.
    
    Provides a mapping of 2-character TOC codes to full company names.
    Data is based on Network Rail open data TOC codes.
    """
    
    # TOC reference data (updated as of 2024)
    # Source: Network Rail Open Data Wiki (https://wiki.openraildata.com/index.php/TOC_Codes)
    # and Rail Data Marketplace
    # 
    # Fields:
    # - name: Full operator name
    # - sector: Type of operator (Passenger, Freight, etc.)
    # - atoc_code: ATOC (Association of Train Operating Companies) 3-letter code
    # - business_code: 2-letter business code used in Network Rail feed URLs (e.g., CIF_HU_TOC_FULL_DAILY)
    #                  This is the actual business code as defined in rail industry standards
    # - sector_code: Numeric sector code that appears in TRUST messages for TOC identification
    #                These were previously (incorrectly) stored in business_code field
    # - legacy_codes: List of historical codes that may appear in feeds
    TOC_DATA = {
        'AW': {'name': 'Arriva Trains Wales / Transport for Wales', 'sector': 'Passenger', 'atoc_code': 'ATW'},
        'CC': {'name': 'c2c', 'sector': 'Passenger', 'atoc_code': 'CCR', 'sector_code': '23'},
        'CH': {'name': 'Chiltern Railways', 'sector': 'Passenger', 'atoc_code': 'CHR', 'sector_code': '74'},
        'CS': {'name': 'Caledonian Sleeper', 'sector': 'Passenger', 'atoc_code': 'CSL', 'sector_code': '85'},
        'EM': {'name': 'East Midlands Railway', 'sector': 'Passenger', 'atoc_code': 'EMR', 'business_code': 'EM', 'sector_code': '28'},
        'ES': {'name': 'Eurostar', 'sector': 'Passenger', 'atoc_code': 'EST', 'sector_code': '28'},
        'EX': {'name': 'Express Passenger', 'sector': 'Passenger'},
        'FC': {'name': 'First Capital Connect', 'sector': 'Passenger', 'atoc_code': 'FCC'},
        'GC': {'name': 'Grand Central', 'sector': 'Passenger', 'atoc_code': 'GCR', 'sector_code': '22'},
        'GN': {'name': 'Great Northern', 'sector': 'Passenger', 'atoc_code': 'GNR'},
        'GR': {'name': 'LNER (London North Eastern Railway)', 'sector': 'Passenger', 'atoc_code': 'LNR', 'sector_code': '24'},
        'GW': {'name': 'Great Western Railway', 'sector': 'Passenger', 'atoc_code': 'GWR', 'sector_code': '79'},
        'GX': {'name': 'Gatwick Express', 'sector': 'Passenger', 'atoc_code': 'GX', 'sector_code': '26'},
        'HC': {'name': 'Heathrow Connect', 'sector': 'Passenger', 'atoc_code': 'HEX'},
        'HT': {'name': 'Hull Trains', 'sector': 'Passenger', 'atoc_code': 'HT', 'sector_code': '80'},
        'HX': {'name': 'Heathrow Express', 'sector': 'Passenger', 'atoc_code': 'HEX', 'sector_code': '29'},
        'IL': {'name': 'Island Line', 'sector': 'Passenger', 'atoc_code': 'IL'},
        'LE': {'name': 'Greater Anglia', 'sector': 'Passenger', 'atoc_code': 'LEA'},
        'LM': {'name': 'West Midlands Railway', 'sector': 'Passenger', 'atoc_code': 'LMR', 'sector_code': '72'},
        'LN': {'name': 'London Northwestern Railway', 'sector': 'Passenger', 'atoc_code': 'LNW', 'sector_code': '86'},
        'LO': {'name': 'London Overground', 'sector': 'Passenger', 'atoc_code': 'LOO', 'sector_code': '87'},
        'LT': {'name': 'London Underground', 'sector': 'Passenger', 'atoc_code': 'LUL', 'sector_code': '91'},
        'ME': {'name': 'Merseyrail', 'sector': 'Passenger', 'atoc_code': 'MER', 'sector_code': '65'},
        'NC': {'name': 'Northern Trains', 'sector': 'Passenger', 'atoc_code': 'NT', 'sector_code': '60'},
        'NT': {'name': 'Northern Rail', 'sector': 'Passenger', 'atoc_code': 'NT'},
        'NY': {'name': 'North Yorkshire Moors Railway', 'sector': 'Heritage'},
        'PE': {'name': 'Penmere', 'sector': 'Freight'},
        'PO': {'name': 'Provincial', 'sector': 'Passenger'},
        'SE': {'name': 'Southeastern', 'sector': 'Passenger', 'atoc_code': 'SET', 'business_code': 'HU', 'sector_code': '80'},
        'SJ': {'name': 'South West Trains / Stagecoach', 'sector': 'Passenger', 'atoc_code': 'SWT'},
        'SN': {'name': 'Southern', 'sector': 'Passenger', 'atoc_code': 'SOU', 'business_code': 'HW', 'sector_code': '88'},
        'SR': {'name': 'ScotRail', 'sector': 'Passenger', 'atoc_code': 'SCO', 'business_code': 'HA', 'sector_code': '60'},
        'SW': {'name': 'South Western Railway', 'sector': 'Passenger', 'atoc_code': 'SWR', 'business_code': 'HY', 'sector_code': '84'},
        'SX': {'name': 'Stansted Express', 'sector': 'Passenger', 'atoc_code': 'SX'},
        'TL': {'name': 'Thameslink', 'sector': 'Passenger', 'atoc_code': 'TLK'},
        'TP': {'name': 'TransPennine Express', 'sector': 'Passenger', 'atoc_code': 'TPE', 'sector_code': '20'},
        'TW': {'name': 'Transport for Wales Rail', 'sector': 'Passenger', 'atoc_code': 'TFW', 'sector_code': '83'},
        'VT': {'name': 'Avanti West Coast', 'sector': 'Passenger', 'atoc_code': 'AVC', 'business_code': 'HF', 'sector_code': '65'},
        'WR': {'name': 'West Coast Railway Company', 'sector': 'Charter', 'atoc_code': 'WCR'},
        'XC': {'name': 'CrossCountry', 'sector': 'Passenger', 'atoc_code': 'XCT', 'sector_code': '27'},
        'XR': {'name': 'Elizabeth Line', 'sector': 'Passenger', 'atoc_code': 'ELZ', 'sector_code': '92'},
        'ZZ': {'name': 'Unidentified', 'sector': 'Unknown'},
        # Freight operators
        'DB': {'name': 'DB Cargo UK', 'sector': 'Freight', 'atoc_code': 'DBC'},
        'DG': {'name': 'Direct Rail Services', 'sector': 'Freight', 'atoc_code': 'DRS'},
        'DQ': {'name': 'Devon & Cornwall Railways', 'sector': 'Freight'},
        'DR': {'name': 'Direct Rail Services', 'sector': 'Freight', 'atoc_code': 'DRS'},
        'EA': {'name': 'Europorte', 'sector': 'Freight'},
        'ED': {'name': 'Edison Rail', 'sector': 'Freight'},
        'FL': {'name': 'First Greater Western Link', 'sector': 'Freight'},
        'FR': {'name': 'Freightliner', 'sector': 'Freight', 'atoc_code': 'FRE'},
        'FS': {'name': 'Freightliner Heavy Haul', 'sector': 'Freight', 'atoc_code': 'FHH'},
        'GB': {'name': 'GBRf (GB Railfreight)', 'sector': 'Freight', 'atoc_code': 'GBR'},
        'GV': {'name': 'Govia', 'sector': 'Freight'},
        'RF': {'name': 'Railfreight', 'sector': 'Freight'},
        'RM': {'name': 'Rail Operations Group', 'sector': 'Freight', 'atoc_code': 'ROG'},
        'RT': {'name': 'Rail Operations Group', 'sector': 'Freight', 'atoc_code': 'ROG'},
        'WH': {'name': 'West Highland Railway', 'sector': 'Freight'},
        # Network Rail and test
        'NR': {'name': 'Network Rail', 'sector': 'Infrastructure'},
        'NW': {'name': 'Network Rail West', 'sector': 'Infrastructure'},
        'NT': {'name': 'Network Rail Test Train', 'sector': 'Test'},
        'TT': {'name': 'Test Train', 'sector': 'Test'},
        'XX': {'name': 'Test / Unknown', 'sector': 'Test'},
    }
    
    def __init__(self) -> None:
        self.toc_map: Dict[str, str] = {}
        self.atoc_to_canonical: Dict[str, str] = {}  # Maps ATOC codes to canonical 2-char codes
        self.business_to_canonical: Dict[str, str] = {}  # Maps business codes to canonical 2-char codes
        self.sector_to_canonical: Dict[str, str] = {}  # Maps sector codes to canonical 2-char codes
        self._load_static_data()
    
    def _load_static_data(self) -> None:
        """Load static TOC data into the resolver and build mapping indices."""
        for code, data in self.TOC_DATA.items():
            self.toc_map[code] = data['name']
            
            # Build ATOC code mapping
            if 'atoc_code' in data:
                atoc = data['atoc_code'].upper()
                self.atoc_to_canonical[atoc] = code
            
            # Build business code mapping
            if 'business_code' in data:
                business = data['business_code']
                self.business_to_canonical[business] = code
            
            # Build sector code mapping (for TRUST message normalization)
            if 'sector_code' in data:
                sector = data['sector_code']
                self.sector_to_canonical[sector] = code
    
    def resolve_toc_code(self, incoming: Optional[str]) -> Optional[str]:
        """
        Resolve an incoming TOC identifier to the canonical 2-character TOC code.
        
        Handles various identifier formats:
        - Canonical 2-character codes (e.g., 'SW', 'GW') - returned as-is
        - ATOC 3-letter codes (e.g., 'SWR', 'GWR') - mapped to canonical code
        - 2-letter business codes (e.g., 'HY', 'HU') - mapped to canonical code
        - Numeric sector codes (e.g., '84', '80') - mapped to canonical code (for TRUST messages)
        
        Args:
            incoming: TOC identifier from message (may be None, canonical, ATOC, business, or sector code)
            
        Returns:
            Canonical 2-character TOC code if mapping found, None otherwise
        """
        if not incoming:
            return None
        
        # Normalize input
        code = incoming.strip().upper()
        if not code:
            return None
        
        # Check if it's already a canonical 2-character code
        if code in self.toc_map:
            return code
        
        # Check if it's an ATOC code
        if code in self.atoc_to_canonical:
            return self.atoc_to_canonical[code]
        
        # Check if it's a business code
        if code in self.business_to_canonical:
            return self.business_to_canonical[code]
        
        # Check if it's a sector code (for TRUST message compatibility)
        if code in self.sector_to_canonical:
            return self.sector_to_canonical[code]
        
        # No mapping found
        return None
    
    def get_toc_name(self, toc_code: str) -> Optional[str]:
        """
        Get the full name for a TOC code.
        
        Args:
            toc_code: 2-character TOC code (e.g., 'SW', 'GW')
            
        Returns:
            Full TOC name if found, None otherwise
        """
        if not toc_code:
            return None
        code = toc_code.strip().upper()
        return self.toc_map.get(code)
    
    def get_business_code(self, toc_code: str) -> Optional[str]:
        """
        Get the business code for a TOC code.
        
        Args:
            toc_code: 2-character TOC code (e.g., 'SE', 'GW')
            
        Returns:
            Business code if found, None otherwise
        """
        if not toc_code:
            return None
        code = toc_code.strip().upper()
        toc_data = self.TOC_DATA.get(code)
        if toc_data:
            return toc_data.get('business_code')
        return None
    
    def get_all_tocs(self) -> List[Dict[str, str]]:
        """
        Get all TOC reference data.
        
        Returns:
            List of dicts with keys: toc_code, toc_name, sector
        """
        result = []
        for code, data in sorted(self.TOC_DATA.items()):
            result.append({
                'toc_code': code,
                'toc_name': data['name'],
                'sector': data.get('sector', 'Unknown')
            })
        return result
    
    def populate_database(self, db: Any, quiet: bool = False) -> int:
        """
        Populate the database with TOC reference data.
        
        Args:
            db: RailDB instance
            quiet: If True, suppress log messages
            
        Returns:
            Number of TOC entries inserted/updated
        """
        count = 0
        for code, data in self.TOC_DATA.items():
            try:
                db.upsert_toc(
                    toc_code=code,
                    toc_name=data['name'],
                    business_code=data.get('business_code'),
                    sector_code=data.get('sector_code'),
                    atoc_code=data.get('atoc_code'),
                    sector=data.get('sector')
                )
                count += 1
            except Exception as e:
                if not quiet:
                    logger.error(f"Failed to insert TOC {code}: {e}")
        
        if not quiet:
            logger.info(f"Populated {count} TOC entries in database")
        
        return count
