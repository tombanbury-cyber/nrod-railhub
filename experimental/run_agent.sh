#!/usr/bin/env bash
# Small wrapper to run an agent while enforcing the working root.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_ROOT="${AGENT_ROOT:-$ROOT_DIR}"

if [[ "$(realpath "$AGENT_ROOT")" != *"/experimental" ]]; then
  echo "ERROR: AGENT_ROOT must be the experimental/ directory. Refusing to run."
  echo "AGENT_ROOT=$AGENT_ROOT"
  exit 2
fi

# Example: ensure the agent reads the manifest before running
MANIFEST="$AGENT_ROOT/AGENT_MANIFEST.yaml"
if [[ ! -f "$MANIFEST" ]]; then
  echo "ERROR: Manifest not found at $MANIFEST"
  exit 3
fi

# Example invocation — replace `your-agent-binary` with the agent runtime.
# The agent should be configured to read the manifest at $MANIFEST.
echo "Running agent with root $AGENT_ROOT and manifest $MANIFEST"
# your-agent-binary --root "$AGENT_ROOT" --manifest "$MANIFEST"
# For now just list the allowed files for a sanity check:
echo "Allowed files under $AGENT_ROOT:"
find "$AGENT_ROOT" -maxdepth 3 -type f | sed -e "s|$ROOT_DIR/||"
