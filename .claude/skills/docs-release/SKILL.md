---
name: docs-release
description: Documentation conventions and release preparation for capturekit — which docs to update for which change, capability-matrix honesty rules, changelog, versioning, packaging checks (build, twine check, wheel contents). Use when updating docs or preparing a release. Publishing requires explicit user approval.
---

# Documentation and release preparation

## Doc map
| File | Update when |
| --- | --- |
| `README.md` | install / quickstart / feature summary changes |
| `docs/capabilities.md` | any feature status change (implemented / experimental / untested / unsupported) |
| `docs/architecture.md` | design changes |
| `docs/decisions.md` | new decision or dependency |
| `docs/guide/*.md` | user-facing API or platform setup changes |
| `docs/PROGRESS.md` | every working session |
| `CHANGELOG.md` | every user-visible change (Keep a Changelog format) |

## Capability honesty rules
- **Implemented**: real backend + tests executed on that platform.
- **Experimental**: works in manual/limited tests; known gaps listed.
- **Untested**: code exists but not executed on that platform/config.
- **Unsupported**: not implemented or out of scope (raises a typed error).
A stub or interface alone is never "implemented".

## Examples
Every code example in docs must be runnable; keep them in `examples/` and
reference them from docs. Run `python examples/<x>.py --help` (or a smoke
mode) before release.

## Release checklist (do NOT publish without user approval)
1. `ruff check . && ruff format --check . && mypy src`
2. Full test run on Windows + Xvfb (WSL); paste the summary (passed/skipped/
   failed counts and skip reasons) into `docs/PROGRESS.md`.
3. Bump `src/capturekit/_version.py`; update CHANGELOG.
4. `python -m build` → `twine check dist/*`; inspect wheel contents
   (`python -m zipfile -l dist/*.whl`) — no tests, no scratch files.
5. Install wheel in a fresh venv, run `examples/list_sources.py`.
6. Ask the user before `twine upload` / tagging.
