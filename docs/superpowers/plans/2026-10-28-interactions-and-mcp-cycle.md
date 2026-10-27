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

Files: `agent_memory/operational.py`, `config.py`, `heartbeat.py`, `migrations.py`, `schemas/timeline_episodic.sql`, `server.py`, `tests/test_operational.py`.

Interfaces: `ConfigStore.transaction()` supplies a write transaction; `OperationalStore.history(user_id, offset=0, limit=50)` returns a bounded history page. `record_interaction(..., interaction_id=None)` deduplicates normalized requests.

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

Files: `agent_memory/autonomy.py`, `server.py`, `tests/test_autonomy.py`.

Interfaces: `AutonomyService.claim(url)`, `.observe(run_id)`, `.act(run_id, action_id, action, arguments)`, `.verify(run_id, text)`; all return JSON dictionaries. MCP tools wrap these methods and paginated interaction history.

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
- [ ] Review diff, commit the requested implementation and push main; inspect CI for the pushed SHA.

## Verification record

Full existing suite: 406 passing before the final browser retry regression; affected browser/operational/autonomy check after that change: 104 passing. Wheel verification and publication receipts are reported separately in the task completion. Real client scheduling and external-account delivery remain outside the local checks.
