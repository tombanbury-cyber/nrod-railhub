# Network Rail Reference Import Service Deployment

This directory contains deployment configuration for the Network Rail reference data import service.

## What is this service?

The `ref-import.service` is a systemd unit that runs a continuous background process to keep Network Rail CORPUS (location data) and SMART (berth stepping) reference data up-to-date. It downloads and imports the data on a configurable schedule (default: every 24 hours).

## Quick Setup

### 1. Install the application

```bash
# Create user
sudo useradd -r -s /bin/false railhub

# Install application
sudo mkdir -p /opt/nrod-railhub /var/lib/nrod-railhub
sudo cp -r nrod_railhub /opt/nrod-railhub/
sudo cp -r import_scripts /opt/nrod-railhub/
sudo chown -R railhub:railhub /opt/nrod-railhub /var/lib/nrod-railhub

# Install Python dependencies
# Option 1: Using virtual environment (recommended)
cd /opt/nrod-railhub
sudo -u railhub python3 -m venv venv
sudo -u railhub venv/bin/pip install stomp.py flask requests

# Option 2: Using system packages (if available)
# sudo apt-get install python3-stomp.py python3-flask python3-requests

# Option 3: Global install (not recommended for production)
# sudo pip3 install stomp.py flask requests
```

### 2. Configure and install the service

```bash
# Copy the unit file
sudo cp deployment/ref-import.service /etc/systemd/system/

# Edit to set your Network Rail credentials
sudo nano /etc/systemd/system/ref-import.service
# Update these lines:
#   Environment="NR_USERNAME=your.email@example.com"
#   Environment="NR_PASSWORD=yourpassword"
```

### 3. Enable and start

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable service to start on boot
sudo systemctl enable ref-import

# Start the service
sudo systemctl start ref-import

# Check status
sudo systemctl status ref-import
```

## Viewing Logs

```bash
# View recent logs
sudo journalctl -u ref-import -n 100

# Follow logs in real-time
sudo journalctl -u ref-import -f

# View logs from last boot
sudo journalctl -u ref-import -b
```

## Configuration Options

Edit `/etc/systemd/system/ref-import.service` to configure:

| Variable | Description | Default |
|----------|-------------|---------|
| `NR_USERNAME` | Network Rail username (required) | - |
| `NR_PASSWORD` | Network Rail password (required) | - |
| `DB_PATH` | SQLite database path | `/var/lib/nrod-railhub/nrod_ref.sqlite` |
| `OUTDIR` | Download directory | `/var/lib/nrod-railhub/ref_downloads` |
| `REF_IMPORT_INTERVAL` | Import interval in seconds | `86400` (24 hours) |
| `DATASETS` | Datasets to import | `CORPUS,SMART` |

After changing configuration:
```bash
sudo systemctl daemon-reload
sudo systemctl restart ref-import
```

## Testing

To test the import manually without the service:

```bash
# Run once with existing files (no download)
python3 import_scripts/nrod_ref_import.py \
  --db /tmp/test.db \
  --username test@example.com \
  --password testpass \
  --outdir json \
  --no-download

# Or download fresh data
python3 import_scripts/nrod_ref_import.py \
  --db /tmp/test.db \
  --username YOUR_EMAIL \
  --password YOUR_PASS
```

## Using with the Main Application

The main nrod-railhub application can read location data from the same database:

```python
# In your code or configuration
from nrod_railhub.database import RailDB

# Open the reference database (read-only safe with WAL mode)
ref_db = RailDB("/var/lib/nrod-railhub/nrod_ref.sqlite")
```

Both the service and application use WAL (Write-Ahead Logging) mode, which allows:
- Multiple readers simultaneously
- One writer (the import service)
- No blocking between readers and writer

## Troubleshooting

### Service won't start

Check logs:
```bash
sudo journalctl -u ref-import -n 50
```

Common issues:
- Missing credentials → Set `NR_USERNAME` and `NR_PASSWORD`
- Permission denied → Check `chown -R railhub:railhub /var/lib/nrod-railhub`
- Python module not found → Install dependencies (see setup instructions)
  - With venv: `sudo -u railhub /opt/nrod-railhub/venv/bin/pip install stomp.py flask requests`
  - System packages: `sudo apt-get install python3-stomp.py python3-flask python3-requests`

### Import failures

The service logs errors but continues running. Check:
```bash
sudo journalctl -u ref-import | grep ERROR
```

Common issues:
- Network timeout → Service will retry on next cycle
- Invalid credentials → Update credentials in unit file
- Disk full → Clean up `/var/lib/nrod-railhub/ref_downloads`

### Stop the service

```bash
# Stop (can restart)
sudo systemctl stop ref-import

# Disable (won't start on boot)
sudo systemctl disable ref-import

# Remove completely
sudo systemctl stop ref-import
sudo systemctl disable ref-import
sudo rm /etc/systemd/system/ref-import.service
sudo systemctl daemon-reload
```

## Manual Import

If you prefer manual updates instead of the continuous service:

```bash
# Download and import once
python3 import_scripts/nrod_ref_import.py \
  --db /var/lib/nrod-railhub/nrod_ref.sqlite \
  --username YOUR_EMAIL \
  --password YOUR_PASS

# Or schedule with cron (run daily at 3 AM)
echo "0 3 * * * cd /opt/nrod-railhub && python3 import_scripts/nrod_ref_import.py --db /var/lib/nrod-railhub/nrod_ref.sqlite --username YOUR_EMAIL --password YOUR_PASS" | sudo crontab -u railhub -
```

## Security Notes

- Store credentials securely (systemd unit file should be readable only by root)
- The service runs as the `railhub` user (non-root)
- Database and downloads are stored in `/var/lib/nrod-railhub` (owned by railhub user)
- Consider using systemd credentials for even better security (see systemd docs)

```bash
# Secure the unit file
sudo chmod 600 /etc/systemd/system/ref-import.service
```

## Monitoring

Check if the service is running:
```bash
sudo systemctl is-active ref-import
```

Check next scheduled run (look at logs):
```bash
sudo journalctl -u ref-import -n 10 | grep "Next import"
```

Check database size:
```bash
du -h /var/lib/nrod-railhub/nrod_ref.sqlite*
```

Verify data freshness:
```bash
sqlite3 /var/lib/nrod-railhub/nrod_ref.sqlite \
  "SELECT dataset, downloaded_at FROM meta_downloads"
```
