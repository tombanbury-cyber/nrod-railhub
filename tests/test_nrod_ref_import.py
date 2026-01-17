import os
import shutil
import sqlite3
import subprocess


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
