# Changelog

Notable changes to machine-driver. Versioning is slow + incremental (`0.0x`), matching the Frontier Infra family.

## 0.1.0 — 2026-06-14

First public skeleton + iteration-1 hardening. The dumb, tireless driver for **code** workloads — Box 2 of The Machine.

- Deterministic loop, **zero model tokens**; atomic checkpoint/resume; one fresh worker process per task; verify-by-ground-truth (exit code) before any durable mutation.
- **Box 0 contract gate** — `commit` blocked unless the contract is independently ratified (`ratified_by != proposed_by`).
- **Box 5 governor** — goal-level budget (`max_worker_runs` / `max_wall_seconds`); breach → HALT + operator alert. Closes the 131-duplicate / thermal-runaway incident class.
- **Autonomy** — `effective = min(mode, contract.autonomy_ceiling, verifier_trust)`; no verifier ⇒ propose-only.
- **Escalation** — block / quarantine / budget-halt fire a Telegram alert, never a silent stdout line.
- Hash-chained driver-loop audit log.
- Smoke tests: happy path · governor halt · no-verifier→propose · hash-chain integrity.

### Next (iteration 2)
- Canonical **signed AAR** per task via the org's `aar.mjs` (→ conformance **L2**). See `README.md` and `CONFORMANCE.md`.
