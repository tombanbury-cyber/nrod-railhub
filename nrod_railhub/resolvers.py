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
