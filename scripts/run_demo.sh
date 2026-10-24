#!/bin/bash
# Demo script for running the schematic visualization PoC

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="$PROJECT_ROOT/railhub.db"

echo -e "${BLUE}NROD RailHub - Schematic Visualization PoC Demo${NC}"
echo "=================================================="
echo

# Step 1: Initialize database
echo -e "${GREEN}Step 1: Initializing database...${NC}"
if [ -f "$DB_PATH" ]; then
    echo -e "${YELLOW}Warning: Database $DB_PATH already exists.${NC}"
    read -p "Do you want to recreate it? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm "$DB_PATH"
        echo "Removed existing database."
    else
        echo "Using existing database."
    fi
fi

if [ ! -f "$DB_PATH" ]; then
    echo "Creating database at $DB_PATH"
    sqlite3 "$DB_PATH" < "$PROJECT_ROOT/sql/init_db.sql"
    echo -e "${GREEN}✓ Database initialized${NC}"
else
    echo -e "${GREEN}✓ Database already exists${NC}"
fi
echo

# Step 2: Install dependencies
echo -e "${GREEN}Step 2: Checking dependencies...${NC}"
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo "Installing Python dependencies..."
    pip install -q fastapi uvicorn[standard] websockets
    echo -e "${GREEN}✓ Dependencies installed${NC}"
else
    echo -e "${GREEN}✓ Dependencies already installed${NC}"
fi
echo

# Step 3: Start the server
echo -e "${GREEN}Step 3: Starting FastAPI server...${NC}"
echo "Server will start at: http://127.0.0.1:8000"
echo "UI will be available at: http://127.0.0.1:8000/static/ui.html"
echo
echo -e "${BLUE}To test the API manually, use these curl commands in another terminal:${NC}"
echo
echo "# Enter a berth:"
echo "curl -X POST http://127.0.0.1:8000/event \\"
echo "  -H 'Content-Type: application/json' \\"
echo "  -d '{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"source\":\"td\",\"train_id\":\"T1\",\"event_type\":\"berth_enter\",\"object_id\":\"BRTH_1\",\"payload\":{}}'"
echo
echo "# Exit a berth:"
echo "curl -X POST http://127.0.0.1:8000/event \\"
echo "  -H 'Content-Type: application/json' \\"
echo "  -d '{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"source\":\"td\",\"train_id\":\"T1\",\"event_type\":\"berth_exit\",\"object_id\":\"BRTH_1\",\"payload\":{}}'"
echo
echo "# Get train journey chain:"
echo "curl http://127.0.0.1:8000/train/T1/chain"
echo
echo -e "${YELLOW}Press Ctrl+C to stop the server${NC}"
echo

cd "$PROJECT_ROOT"
exec python3 -m uvicorn app.visualisation.app:app --reload --host 127.0.0.1 --port 8000
