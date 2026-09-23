# Schematic Visualization API

FastAPI backend for real-time train schematic visualization.

The demo UI polls `GET /state` to rebuild the current view from persisted TD
state (`td_state` and `td_berth_events`) while keeping `POST /event` available
for manual demo injections.

## Quick Start

```bash
# Initialize database
sqlite3 railhub.db < sql/init_db.sql

# Start server
python3 -m uvicorn app.visualisation.app:app --reload --host 127.0.0.1 --port 8000

# Open UI
# Navigate to: http://127.0.0.1:8000/static/ui.html
```

## API Documentation

Once the server is running, visit:
- API docs: http://127.0.0.1:8000/docs
- Alternative docs: http://127.0.0.1:8000/redoc

## See Also

Comprehensive documentation: [docs/visualisation.md](../../docs/visualisation.md)
