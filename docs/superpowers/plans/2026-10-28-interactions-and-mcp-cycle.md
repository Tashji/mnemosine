# Interactions and MCP cycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Atomic and idempotent interactions, SQL history, durable browser work for the connected MCP agent.

**Architecture:** Store authoritative operational documents, interaction history and request receipts in the existing timeline database. ConfigStore coordinates SQL transactions and compatibility exports. A separate autonomy module coordinates browser actions without hosting a model.

**Tech Stack:** Python 3.12, sqlite3, existing MCP and Playwright interfaces, unittest.

## Global Constraints

- No additional service, model API, site-specific adapter or fifth database.
- Existing security flags and explicit per-origin consent remain required.
- Browser observations never constitute proof of remote delivery.
- Keep the original JSON during import; export failures cannot undo a committed transaction.
- Verify the installed wheel and preserve the user's profiles.

### Task 1: Operational transactions and SQL history

Files: `agent_memory/operational.py`, `config.py`, `heartbeat.py`, `migrations.py`, `server.py`, `tests/test_operational.py`. The new tables are created by the versioned steps `_timeline_v2` and `_timeline_v3` in `agent_memory/migrations.py`; the packaged base schema `agent_memory/schemas/timeline_episodic.sql` keeps only the episodic tables, and a new profile reaches the current version by running the migration steps from the baseline.

Interfaces: `ConfigStore.transaction()` supplies a write transaction; `OperationalStore.history(user_id, *, offset=0, limit=50)` returns a bounded history page, with `offset` and `limit` keyword-only and `limit` capped at 100. `HeartbeatEngine.record_interaction(..., interaction_id=None)` deduplicates normalized requests; the MCP tool of the same name in `server.py` forwards the optional ID.

- [x] Add failing tests for duplicate IDs, conflicting payloads, rollback, concurrent requests and import preservation.
- [x] Add versioned SQL tables for operational documents, interactions, receipts and autonomy execution records.
- [x] Implement one-time import and authoritative reads; route ConfigStore writes to the same connection inside transactions. Export operational documents after commit, reporting export errors separately.
- [x] Read current load inside the transaction; validate references and insert event/receipt together with updated state and graph.
- [x] Expose the optional ID in CLI and MCP; run focused operational and heartbeat tests.

Transaction algorithm:
```python
with config.transaction() as conn:
    receipt = conn.execute('SELECT payload, result FROM interaction_receipts WHERE id = ?', (interaction_id,)).fetchone()
    # If present, compare canonical payload and return the saved result.
    # Otherwise read current state, validate, insert event and update both documents.
```

### Task 2: Dashboard and contact consistency

Files: `dashboard.py`, `dashboard_settings.py`, `dashboard_assets/app.js`, `service.py`, dashboard tests.

Interfaces: `OperationalStore.history` supplies total, offset, limit and entries; graph returns summaries without expanding history.

- [x] Test SQL pagination above 100 events, same-timestamp ordering, contact deletion/recreation and summary consistency.
- [x] Fetch only selected contact history; fetch latest event separately for overview.
- [x] Remove contact and events within one transaction, retaining episodic memories and WhatsApp policy.
- [x] Update existing fixtures to write authoritative operational state through ConfigStore instead of editing exports.
- [x] Run dashboard, executor and MCP checks covering changed consumers.

### Task 3: Agent-owned autonomous browser cycle

Files: `agent_memory/autonomy.py`, `server.py`, `tests/test_autonomy.py`. The administrative review of uncertain runs arrived after this task and was outside its scope: `AutonomyService.review_summary` and `.review_uncertain`, the `autonomous_reviews` table (`_timeline_v3`), the `POST /api/autonomy/review` route in `dashboard.py` and its panel in `dashboard_assets/app.js`.

Interfaces: `AutonomyService.claim(url)`, `.status(run_id)`, `.observe(run_id)`, `.act(run_id, action_id, action, arguments)`, `.verify(run_id, text)`, `.abort(run_id)`; all return JSON dictionaries. Six MCP tools wrap them (`autonomous_claim`, `autonomous_status`, `autonomous_observe`, `autonomous_act`, `autonomous_verify`, `autonomous_abort`), next to the paginated `interaction_history` tool.

- [x] Test pending impulse acquisition without opening a browser, bounded steps, expiry, maintenance and revoked consent.
- [x] Persist execution and unique action receipt before touching the browser; duplicate completed calls return original observation, pending calls remain uncertain.
- [x] Observe using existing browser controller and verify explicit expected text; close only the original impulse after successful UI verification.
- [x] Test a fake browser success path and interrupted-action recovery. No real external messages.

Action algorithm:
```python
# Transaction 1: authorize, reserve action_id and increment step count.
# Execute the whitelisted controller operation outside SQLite.
# Transaction 2: save observation and outcome.
# An interruption between these steps leaves an uncertain action, never auto-retried.
```

### Task 4: Documentation, package, commit and push

Files: README, pyproject, package version, affected tests and plan.

- [x] Document JSON export semantics, ID requirements, client scheduling and UI-only verification.
- [x] Run focused tests and the existing suite when required for the common ConfigStore change; fix actual failures rather than suppressing them.
- [x] Build wheel, install separately and check old-profile migration, SQL pages, console scripts, MCP handshake and browser consent.
- [x] Start/reuse authenticated dashboard on port 3000, verify HTTP readiness and show preview.
- [x] Review diff, commit the requested implementation and push main; inspect CI for the pushed SHA.

## Verification record

Work published on `main` in `0284a14` ("Esegui una sola volta ogni test nella discovery"), CI GitHub completed with success on that SHA. The four tasks above and the follow-up dashboard work are therefore all committed and pushed.

### Check of this document against the code (2026-10-29)

Every claim above was re-read against the current tree, and the full suite was run again: `Ran 452 tests in 58.421s`, `OK`, so the count has not drifted since `0284a14`. Confirmed as written: one write transaction in `ConfigStore.transaction()`, exports after the commit with their own warning (`operational.py`), the one-time import that moves history out of the stored graph, four SQLite databases, `__version__` `0.8.0` matching `pyproject.toml`, contact removal deleting events inside the same transaction while keeping episodic memories and the WhatsApp policy, and the autonomy interfaces. Corrected in this revision: the operational tables live in the migration steps of `agent_memory/migrations.py`, not in the packaged base schema file; the `history` signature is keyword-only with `limit` capped at 100; and the autonomy section now lists `status` and `abort` plus the later review surface. The checkbox/count update was published in `fe830df`; this code-review revision was published in `f3323d5`, on top of it.

Full suite: **452 tests, OK**, run with `python -m unittest discover -s tests -t .` from the repository root. The earlier count of 406 referred to an intermediate state before the browser retry regression was fixed; the count then rose to 458, but that number was inflated because `tests/` was not a package and seven scenarios ran twice or three times. Commit `0284a14` fixed the double discovery (452 distinct ids, guarded by `tests/test_discovery.py`), so 458 must not be read as additional coverage. The affected browser/operational/autonomy check stood at 104 passing at the time it was run. Wheel verification and publication receipts are reported separately in the task completion. Real client scheduling and external-account delivery remain outside the local checks.
