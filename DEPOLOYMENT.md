# Deployment Guide

## Quick Start (Development)

```bash
# Run directly
python3 nrod_railhub.py --user USER --password PASS \
  --db-path ~/rail. db --web-port 8080
```

## Production Deployment

### Option 1: Systemd Service (Recommended for Linux)

Create `/etc/systemd/system/nrod-railhub.service`:

```ini
[Unit]
Description=Network Rail Hub Live Tracker
After=network.target

[Service]
Type=simple
User=railhub
WorkingDirectory=/opt/nrod-railhub
Environment=NR_USER=your. email@example.com
Environment=NR_PASSWORD=yourpassword
ExecStart=/usr/bin/python3 /opt/nrod-railhub/nrod_railhub. py \
  --user ${NR_USER} \
  --password ${NR_PASSWORD} \
  --db-path /var/lib/nrod-railhub/rail.db \
  --web-port 8080
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Setup: 

```bash
# Create user
sudo useradd -r -s /bin/false railhub

# Install application
sudo mkdir -p /opt/nrod-railhub /var/lib/nrod-railhub
sudo cp nrod_railhub.py /opt/nrod-railhub/
sudo chown -R railhub:railhub /opt/nrod-railhub /var/lib/nrod-railhub

# Install dependencies
sudo pip3 install stomp. py flask

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable nrod-railhub
sudo systemctl start nrod-railhub

# Check status
sudo systemctl status nrod-railhub
sudo journalctl -u nrod-railhub -f
```

### Option 2: Docker

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir stomp. py flask

# Copy application
COPY nrod_railhub. py .

# Create cache and data directories
RUN mkdir -p /root/.cache/openraildata /data

# Expose web port
EXPOSE 8080

# Run application
ENTRYPOINT ["python3", "nrod_railhub.py"]
CMD ["--user", "${NR_USER}", "--password", "${NR_PASSWORD}", \
     "--db-path", "/data/rail.db", "--web-port", "8080"]
```

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  railhub:
    build: . 
    environment:
      - NR_USER=${NR_USER}
      - NR_PASSWORD=${NR_PASSWORD}
    ports:
      - "8080:8080"
    volumes: 
      - ./data:/data
      - ./cache:/root/.cache/openraildata
    restart: unless-stopped
```

Run:

```bash
# Create . env file
cat > .env <<EOF
NR_USER=your.email@example.com
NR_PASSWORD=yourpassword
EOF

# Start
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

### Option 3: Supervisor (Alternative to Systemd)

Install supervisor:

```bash
sudo apt-get install supervisor
```

Create `/etc/supervisor/conf.d/nrod-railhub.conf`:

```ini
[program:nrod-railhub]
command=/usr/bin/python3 /opt/nrod-railhub/nrod_railhub. py 
  --user your.email@example.com 
  --password yourpassword 
  --db-path /var/lib/nrod-railhub/rail.db 
  --web-port 8080
directory=/opt/nrod-railhub
user=railhub
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/nrod-railhub. log
```

```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start nrod-railhub
```

## Reverse Proxy (nginx)

For production web dashboard, use nginx reverse proxy:

```nginx
# /etc/nginx/sites-available/railhub

server {
    listen 80;
    server_name railhub.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # Optional: Basic auth
    # auth_basic "Rail Hub";
    # auth_basic_user_file /etc/nginx/.htpasswd;
}
```

Enable: 

```bash
sudo ln -s /etc/nginx/sites-available/railhub /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## Environment Variables (Best Practice)

Instead of command-line passwords, use environment variables: 

Modify code (around line 2150):

```python
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(...)
    p.add_argument("--user", default=os.environ.get("NR_USER"), 
                   help="Network Rail username (or set NR_USER env var)")
    p.add_argument("--password", default=os.environ. get("NR_PASSWORD"),
                   help="Network Rail password (or set NR_PASSWORD env var)")
    # ... 
```

Then run:

```bash
export NR_USER=your.email@example.com
export NR_PASSWORD=yourpassword
python3 nrod_railhub.py --db-path rail.db --web-port 8080
```

## Monitoring

### Health Check

Add to code:

```python
@app.get("/health")
def health():
    return {"status": "ok", "connected": listener.connected_at is not None}
```

### Prometheus Metrics (Future Enhancement)

Could expose: 
- Messages received counter
- Processing latency histogram
- Database size gauge
- Active trains gauge

### Log Aggregation

Systemd journal: 

```bash
sudo journalctl -u nrod-railhub -f --output=json | \
  jq -r '.MESSAGE'
```

Or configure syslog forwarding to ELK/Graylog. 

## Database Maintenance

### Backup

```bash
# While service running (WAL mode safe)
sqlite3 /var/lib/nrod-railhub/rail.db ". backup /backup/rail-$(date +%Y%m%d).db"
```

### Vacuum (Monthly)

```bash
sqlite3 /var/lib/nrod-railhub/rail. db "VACUUM;"
```

### Archive Old Events

```python
# Add to code or run manually
conn. execute("DELETE FROM td_event WHERE ts_utc < date('now', '-30 days')")
conn.commit()
```

## Scaling

### Horizontal Scaling

Current architecture is single-instance. For multiple instances:

1. **Separate receiver and web server**
   - Receiver writes to DB
   - Multiple web servers read from DB (SQLite supports this)

2. **Message queue (Redis/RabbitMQ)**
   - Receiver publishes to queue
   - Multiple workers consume and write to DB

3. **Shared database (PostgreSQL)**
   - Replace SQLite with PostgreSQL
   - Multiple receivers write concurrently

### Vertical Scaling

- **More memory:** Speeds up schedule loading
- **SSD:** Faster SQLite writes
- **More cores:** Doesn't help much (single receiver thread)

## Troubleshooting

### Service Won't Start

```bash
# Check logs
sudo journalctl -u nrod-railhub -n 50

# Common issues:
# - Wrong credentials → check NR_USER/NR_PASSWORD
# - Port already in use → change --web-port
# - Permission denied → check file ownership
```

### High Memory Usage

Schedule loading uses ~500MB temporarily. After loading, should drop to ~100MB.

### Database Locked Errors

WAL mode should prevent this. If it occurs:

```bash
# Check WAL mode
sqlite3 rail.db "PRAGMA journal_mode;"

# Should return "wal".  If not: 
sqlite3 rail.db "PRAGMA journal_mode=WAL;"
```

### Connection Drops

Network Rail occasionally restarts brokers. The `reconnect_attempts_max=5` setting handles this automatically.

## Security Hardening

### Restrict Web Dashboard

Only bind to localhost:

```python
# In start_web_dashboard():
app.run(host="127.0.0.1", port=port, ...)  # Instead of 0.0.0.0
```

Then access via SSH tunnel: 

```bash
ssh -L 8080:localhost:8080 user@server
# Browse to http://localhost:8080
```

### Firewall

```bash
# Only allow SSH and optionally HTTP/HTTPS
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

### File Permissions

```bash
sudo chmod 600 /etc/systemd/system/nrod-railhub.service
sudo chmod 700 /var/lib/nrod-railhub
sudo chmod 600 /var/lib/nrod-railhub/rail.db
```

## Cost Estimates

### Self-Hosted (VPS)

- **Minimal:** $5/month (1GB RAM, 25GB SSD) - Vultr, DigitalOcean, Linode
- **Recommended:** $10/month (2GB RAM, 50GB SSD)
- **Network Rail account:** Free

### Cloud (AWS)

- **EC2 t3.small:** ~$15/month (2 vCPU, 2GB RAM)
- **EBS 50GB:** ~$5/month
- **Data transfer:** Negligible (< 1GB/month)
- **Total:** ~$20/month

Network Rail feed bandwidth: ~10-50 KB/s (< 150GB/month)

## Next Steps

- Set up alerts (email/SMS when connection drops)
- Configure log rotation
- Schedule database backups
- Add monitoring dashboard (Grafana)
- Document disaster recovery procedure
