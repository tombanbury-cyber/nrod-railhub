# Experimental dashboard — scope and rules

Purpose
- This folder contains the experimental NOC-style dashboard using curses and sqlite.
- The codebase under `experimental/` is allowed to change and test data related to the dashboard only.

Human rules (must be followed by any automated agent and humans):
- Allowed paths: any file under `experimental/**`.
- Disallowed paths: any file outside `experimental/**` except:
  - `.github/CODEOWNERS` (for ownership changes only, via PR to root)
  - CI workflow files under `.github/workflows/` (only to add/modify experimental-specific workflows)
- Agents must never write, modify, or delete files outside `experimental/` without an explicit human-approved PR that lists the reason and an approver from CODEOWNERS.
- Agents must not create new top-level packages or projects; any scaffolding must be inside `experimental/`.
- Agents must not leak credentials, nor call external systems except allowed data sources (Network Rail ingestion) and only via credentials stored in approved secrets.

Operational rules:
- Local dev and CI must run tests only inside `experimental/` unless a PR explicitly changes other code and is approved.
- DB files used for dev should be placed under `experimental/data/` or `experimental/tmp/`.
- Logs created by dev/test runs should be written under `experimental/logs/` and must be gitignored if ephemeral.

Change control:
- Any PR that touches files outside `experimental/` requires manual human review and must reference this SCOPE.
- All agent-made commits must include a commit message prefix: `[agent] experimental:` followed by a summary.

Contacts:
- Primary owner(s): @tombanbury-cyber (listed also in CODEOWNERS)
- Use the repository issue template to request exceptions.
