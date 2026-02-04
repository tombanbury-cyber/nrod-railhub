---
description: "Instructions for experimental features directory"
applyTo: "experimental/**"
---

# Experimental Features Guidelines

## Purpose

The `experimental/` directory contains work-in-progress features, prototypes, and exploratory code that may not yet be production-ready.

## Code Ownership

- **All changes require owner review** (see `CODEOWNERS`)
- Changes trigger `experimental-ci.yml` GitHub Actions workflow
- Separate testing pipeline from main codebase

## Development Rules

### Testing
- Each experimental feature should have its own test file
- Tests may fail without blocking main branch CI
- Use `experimental/requirements.txt` for dependencies specific to experiments

### Code Quality
- Experimental code should still follow Python best practices
- Type hints encouraged but not strictly required
- Documentation should explain the experiment's purpose and status

### Integration
- **Do not** import experimental code in main application
- Experimental features must be explicitly opt-in
- Keep experimental dependencies isolated from main requirements.txt

### Graduation Process

When an experimental feature is ready for production:

1. Move code to appropriate location in `nrod_railhub/`
2. Add comprehensive tests in `tests/`
3. Update main documentation
4. Add to main `requirements.txt` if new dependencies
5. Remove from `experimental/`

## Special Files

- `AGENT_MANIFEST.yaml` - Configuration for custom agents
- `SCOPE.md` - Defines boundaries for experimental work
- `.agentignore` - Files to exclude from agent processing

## Examples

Good experimental features:
- New data feed integrations (e.g., RTPPM, BPLAN)
- Alternative visualization approaches
- Performance optimization prototypes
- Database schema experiments

Not suitable for experimental:
- Bug fixes (should go directly to main code)
- Documentation updates
- Dependency updates for existing features
