import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "import_scripts"))
import nrod_ref_import
from nrod_railhub import ref_bootstrap


_real_run_imports = nrod_ref_import.run_imports


def _fake_run_imports(src_dir):
    def fake(db_path, datasets, username, password, outdir, download=True, rebuild=False):
        return _real_run_imports(db_path, datasets, username, password, src_dir, download=False)
    return fake


def test_missing_tables_without_credentials(tmp_path):
    db = str(tmp_path / "t.db")
    sqlite3.connect(db).close()
    assert ref_bootstrap.ensure_reference_data(db, None, None) is False


def test_imports_when_tables_missing_then_skips(tmp_path, monkeypatch):
    src = tmp_path / "json"
    src.mkdir()
    shutil.copy("json/CORPUSExtract.json", str(src / "CORPUS.json"))
    shutil.copy("json/SMARTExtract.json", str(src / "SMART.json"))
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(nrod_ref_import, "run_imports", _fake_run_imports(str(src)))
    assert ref_bootstrap.ensure_reference_data(db, "u", "p") is True
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM corpus_tiploc").fetchone()[0] > 0
    conn.close()

    def boom(*a, **k):
        raise AssertionError("should not re-import")
    monkeypatch.setattr(nrod_ref_import, "run_imports", boom)
    assert ref_bootstrap.ensure_reference_data(db, "u", "p") is True


def test_import_failure_is_handled(tmp_path, monkeypatch):
    def fail(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(nrod_ref_import, "run_imports", fail)
    assert ref_bootstrap.ensure_reference_data(str(tmp_path / "x.db"), "u", "p") is False
