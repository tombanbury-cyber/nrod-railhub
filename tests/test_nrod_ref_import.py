import os
import shutil
import sqlite3
import subprocess
import sys

# Add the import_scripts directory to path for direct imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "import_scripts"))
from nrod_ref_import import run_imports


def test_import_from_included_json(tmp_path):
    # Prepare test data directory with expected filenames
    # The repo has CORPUSExtract.json and SMARTExtract.json,
    # but the importer expects CORPUS.json and SMART.json
    test_json_dir = tmp_path / "json"
    test_json_dir.mkdir()
    
    # Copy the extract files to expected names
    shutil.copy("json/CORPUSExtract.json", str(test_json_dir / "CORPUS.json"))
    shutil.copy("json/SMARTExtract.json", str(test_json_dir / "SMART.json"))
    
    # Run the importer against included JSON extracts using --no-download
    db_path = tmp_path / "test_nrod_ref.sqlite"
    cmd = [
        "python",
        "import_scripts/nrod_ref_import.py",
        "--db", str(db_path),
        "--username", "unused",
        "--password", "unused",
        "--outdir", str(test_json_dir),
        "--no-download"
    ]
    # Using subprocess ensures the script runs as-is; fail on non-zero return
    subprocess.run(cmd, check=True)
    # Basic sanity check: DB exists and contains meta table
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta_downloads'")
        assert cur.fetchone() is not None
    finally:
        conn.close()


def test_run_imports_api(tmp_path):
    """Test the new run_imports() API function directly."""
    # Prepare test data directory
    test_json_dir = tmp_path / "json"
    test_json_dir.mkdir()
    
    # Copy the extract files to expected names
    shutil.copy("json/CORPUSExtract.json", str(test_json_dir / "CORPUS.json"))
    shutil.copy("json/SMARTExtract.json", str(test_json_dir / "SMART.json"))
    
    # Run the importer using the new API
    db_path = tmp_path / "test_api_nrod_ref.sqlite"
    summary = run_imports(
        db_path=str(db_path),
        datasets=["CORPUS", "SMART"],
        username="unused",
        password="unused",
        outdir=str(test_json_dir),
        download=False,
        rebuild=False,
    )
    
    # Verify summary structure
    assert "db_path" in summary
    assert "datasets" in summary
    assert "CORPUS" in summary["datasets"]
    assert "SMART" in summary["datasets"]
    
    # Verify CORPUS results
    corpus_result = summary["datasets"]["CORPUS"]
    assert "TIPLOCDATA" in corpus_result
    assert "STANOXDATA" in corpus_result
    assert "CRSDATA" in corpus_result
    assert corpus_result["TIPLOCDATA"] > 0
    
    # Verify SMART results
    smart_result = summary["datasets"]["SMART"]
    assert "BERTHDATA" in smart_result
    assert smart_result["BERTHDATA"] > 0
    
    # Verify DB was created and has expected tables
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        # Check for main tables
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cur.fetchall()]
        assert "corpus_tiploc" in tables
        assert "corpus_stanox" in tables
        assert "corpus_crs" in tables
        assert "smart_steps" in tables
        assert "meta_downloads" in tables
        
        # Verify WAL mode is enabled
        cur.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0]
        assert mode.lower() == "wal", f"Expected WAL mode, got {mode}"
    finally:
        conn.close()

