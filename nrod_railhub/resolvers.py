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
                print(f"[{utc_now_iso()}] CORPUS: downloading to {path} ...")
            self._download_corpus(username, password, str(path))
        else:
            if not quiet:
                print(f"[{utc_now_iso()}] CORPUS: using cached file {path}")

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
        except Exception as e:
            raise RuntimeError(f"CORPUS parse failed (not valid JSON?): {e}") from e

        # CORPUS can be either:
        #   - a list of dict rows
        #   - a wrapper dict, commonly {"TIPLOCDATA": [ ... ]}
        rows: Optional[List[Any]] = None

        if isinstance(payload, list):
            rows = payload
            if not quiet:
                print(f"[{utc_now_iso()}] CORPUS format: list")
        elif isinstance(payload, dict):
            if not quiet:
                print(f"[{utc_now_iso()}] CORPUS format: dict wrapper keys={list(payload.keys())}")

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

        self.tiploc_to_name = tiploc
        self.stanox_to_name = stanox
        self.crs_to_name = crs

        if not quiet:
            print(
                f"[{utc_now_iso()}] CORPUS loaded: "
                f"{len(tiploc)} TIPLOC, {len(stanox)} STANOX, {len(crs)} CRS mappings"
            )



    def name_for_tiploc(self, code: str) -> str:
        return self.tiploc_to_name.get((code or "").strip().upper(), "")

    def name_for_stanox(self, code: str) -> str:
        return self.stanox_to_name.get((code or "").strip(), "")

    def name_for_crs(self, code: str) -> str:
        return self.crs_to_name.get((code or "").strip().upper(), "")


class SmartResolver:
    """Loads SMART berth stepping reference data and provides TD+berth -> STANOX/platform/name."""

    def __init__(self) -> None:
        # Keyed by (td_area, berth) e.g. ("AD","0152") -> dict with stanox/platform/stanme
        self.berth_map: Dict[Tuple[str, str], Dict[str, str]] = {}

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
                print(f"[{utc_now_iso()}] SMART: downloading to {path} ...")
            # Re-use LocationResolver's downloader (same auth + redirect rules)
            lr = LocationResolver()
            lr._download_corpus(username, password, str(path).replace("CORPUS", "SMART"))  # legacy fallback
            # The above line isn't reliable if path doesn't contain CORPUS; do it properly:
            self._download_smart(username, password, str(path))
        else:
            if not quiet:
                print(f"[{utc_now_iso()}] SMART: using cached file {path}")

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
        except Exception as e:
            raise RuntimeError(f"SMART parse failed (not valid JSON?): {e}") from e

        rows: Optional[List[Any]] = None
        if isinstance(payload, list):
            rows = payload
            if not quiet:
                print(f"[{utc_now_iso()}] SMART format: list")
        elif isinstance(payload, dict):
            if not quiet:
                print(f"[{utc_now_iso()}] SMART format: dict wrapper keys={list(payload.keys())}")
            for key in ("SMARTDATA", "smartdata", "data", "rows", "SMART"):
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
            print(f"[{utc_now_iso()}] SMART loaded: {len(self.berth_map)} berth mappings")

    def lookup(self, td_area: str, berth: str) -> Optional[Dict[str, str]]:
        k = ((td_area or "").strip().upper(), (berth or "").strip().upper())
        return self.berth_map.get(k)


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
                print(f"[{utc_now_iso()}] SCHEDULE: redirect to {loc[:90]}...")
            # Follow redirect to pre-signed S3 URL without Authorization header.
            req2 = urllib.request.Request(loc, headers={"User-Agent": "nrod-schedule-client/1"})
            with urllib.request.urlopen(req2, timeout=180) as resp2:
                data = resp2.read()

        os.makedirs(os.path.dirname(out_gz) or ".", exist_ok=True)
        tmp = out_gz + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, out_gz)
