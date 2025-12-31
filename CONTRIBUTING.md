# Contributing to nrod-railhub

Thanks for your interest in contributing!  This document provides guidelines for development.

## Getting Started

### Development Setup

```bash
# Fork and clone
git clone https://github.com/YOUR_USERNAME/nrod-railhub.git
cd nrod-railhub

# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# or:  venv\Scripts\activate  # Windows

# Install dependencies
pip install stomp.py flask

# Get Network Rail credentials (free)
# Register at:  https://publicdatafeeds.networkrail.co.uk/
```

### Running Tests

```bash
# Manual smoke test
python3 nrod_railhub.py --user USER --password PASS --headcode 2C90 --verbose

# With database and web UI
python3 nrod_railhub.py --user USER --password PASS \
  --db-path test. db --web-port 8080 --headcode 2C90
```

**Note:** Automated tests not yet implemented (contributions welcome!)

## Code Style

### Python Conventions

- **Python version:** 3.9+ (for type hint features)
- **Style guide:** PEP 8 with 120-character line length
- **Type hints:** Required on all public functions
- **Docstrings:** Google-style for public classes/methods

Example: 

```python
def resolve_location(self, tiploc: str, stanox: Optional[str] = None) -> Dict[str, str]:
    """Resolve a TIPLOC and optional STANOX to human-readable location.
    
    Args:
        tiploc: 7-character TIPLOC code (e.g. 'CLPHMJC')
        stanox: Optional 5-digit STANOX code
        
    Returns:
        Dict with keys: 'name', 'crs', 'stanox'
    """
    # Implementation... 
```

### Threading & Concurrency

- **Always** use locks around shared state
- DB operations must use `with self._lock, self._conn:`
- Catch exceptions in receiver thread (log, don't crash)

```python
def on_message(self, frame) -> None:
    try:
        # Process message
        self.db.upsert_train(data)
    except Exception as e:
        # Log first N errors, don't crash thread
        if self._error_count < 5:
            print(f"Error: {e}")
        self._error_count += 1
```

## Areas for Contribution

### High Priority

1. **Automated Testing**
   - Unit tests for LocationResolver, SmartResolver
   - Integration tests with mock STOMP feed
   - Test data fixtures from real messages

2. **Web Dashboard Improvements**
   - Use Jinja2 templates instead of raw HTML
   - Add CSS framework (Bootstrap, Tailwind)
   - Real-time updates (WebSocket or SSE)
   - Export functionality (CSV, JSON)

3. **Performance**
   - Schedule loading optimization (currently ~30s for large files)
   - Database query optimization (add indices)
   - Memory usage profiling

### Medium Priority

4. **Additional Data Sources**
   - BPLAN (planned engineering work)
   - SCHEDULE extract parsing improvements
   - RTPPM (real-time performance data)

5. **Documentation**
   - API documentation (Sphinx)
   - Architecture diagrams (draw.io, PlantUML)
   - Video walkthrough/tutorial

6. **Deployment**
   - Docker container with docker-compose
   - Systemd service file
   - Ansible playbook

### Nice to Have

7. **Features**
   - Email/SMS alerts for specific trains
   - Historical analysis tools
   - Multiple headcode tracking dashboard
   - Export to GPX (train position tracking)

8. **Code Quality**
   - Linting (pylint, flake8, black)
   - Pre-commit hooks
   - GitHub Actions CI/CD
   - Code coverage tracking

## Development Workflow

### Making Changes

1. **Create a branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make changes**
   - Follow code style guidelines
   - Add docstrings for new public functions
   - Update `copilot-instructions.md` if adding major features

3. **Test manually**
   ```bash
   # Test your changes with live data
   python3 nrod_railhub.py --user USER --password PASS --verbose
   ```

4. **Commit with clear messages**
   ```bash
   git commit -m "feat: add BPLAN feed support"
   git commit -m "fix: handle missing STANOX in TD berth lookup"
   git commit -m "docs: update README with new CLI options"
   ```

5. **Push and create PR**
   ```bash
   git push origin feature/your-feature-name
   # Then create PR on GitHub
   ```

### Commit Message Format

Use conventional commits: 

- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation changes
- `style:` - Code style (formatting, no logic change)
- `refactor:` - Code refactoring
- `test:` - Adding/updating tests
- `chore:` - Maintenance tasks

## Common Development Tasks

### Adding a New Reference Data Source

1. Create resolver class: 
   ```python
   class NewResolver: 
       def __init__(self) -> None:
           self.data:  Dict[str, str] = {}
       
       def load_or_download(self, username: str, password: str, 
                           cache_path: str, force:  bool = False) -> None:
           # Implementation using pattern from LocationResolver
   ```

2. Add CLI arguments in `parse_args()`

3. Initialize in `connect_and_run()` and pass to `HumanView`

4. Use in rendering methods

### Adding Database Tables

1. Update `RailDB._init_schema()`:
   ```python
   CREATE TABLE IF NOT EXISTS new_table (
       id INTEGER PRIMARY KEY,
       field TEXT NOT NULL
   );
   CREATE INDEX IF NOT EXISTS idx_new_table_field ON new_table(field);
   ```

2. Add upsert/insert method: 
   ```python
   def upsert_new_data(self, key: str, value: str) -> None:
       with self._lock, self._conn:
           self._conn.execute(
               "INSERT INTO new_table(field) VALUES (?) "
               "ON CONFLICT(field) DO UPDATE SET field=excluded. field",
               (value,)
           )
   ```

3. Call from `Listener.on_message()`

### Testing Reference Data Downloads

```bash
# Force refresh all reference data
python3 nrod_railhub.py --user USER --password PASS \
  --corpus-refresh --smart-refresh --schedule-refresh \
  --verbose

# Check cache location
ls -lh ~/. cache/openraildata/
```

## Network Rail Data Feed Notes

### Authentication & Redirects

Network Rail uses HTTP Basic Auth followed by redirects to S3:

- ✅ Send `Authorization: Basic ... ` to `publicdatafeeds.networkrail.co.uk`
- ❌ **Never** send `Authorization` to S3 URLs (they use `X-Amz-*` query params)
- Follow redirects manually to control headers per-hop

### Message Formats

- Messages are JSON, often wrapped in arrays:  `[{... }, {...}]`
- TD messages wrapped in type-specific keys:  `{"CA_MSG": {...}}`
- TRUST messages have `{"header": {...}, "body": {...}}` structure
- VSTP messages have `{"VSTPCIFMsgV1": {...}}` structure

### Rate Limits & Etiquette

- No explicit rate limits, but be respectful
- Use caching (CORPUS/SMART/SCHEDULE change infrequently)
- Don't hammer download endpoints (they redirect to S3)
- Consider impact if running many instances

## Questions? 

- 💬 Open a [Discussion](https://github.com/tombanbury-cyber/nrod-railhub/discussions)
- 🐛 Found a bug? [Open an Issue](https://github.com/tombanbury-cyber/nrod-railhub/issues)
- 📧 Contact maintainer: [your-email if you want]

## Code of Conduct

- Be respectful and inclusive
- Assume good intentions
- Focus on constructive feedback
- Help others learn

Thank you for contributing!  🚂
