# experimental/

This folder contains the experimental NOC-style dashboard built with curses over sqlite.

How to run locally (developer):
1. Use the provided wrapper: `experimental/run_agent.sh` (edit to call your agent)
2. Keep all code, DB files, logs inside `experimental/`
3. Tests live under `experimental/tests/` — CI will run them automatically for PRs affecting experimental.

Policy & scope:
- See `experimental/SCOPE.md` and `experimental/AGENT_MANIFEST.yaml` for rules that agents and maintainers must follow.