# machine-driver Conformance

machine-driver is a reference implementation of **The Machine** — the six-box harness for
reliable long-running applied intelligence. The standard it conforms to is **The Machine —
Conformance Spec vNext** (canonical: `github.com/frontier-infra/the-machine`). This file is the
honest self-assessment; the **kit is the source of truth** — run it, don't trust this prose.

## Level: **L2 Instrumented** (kit-measured, vNext) — signed receipts now emitted

Run the executable kit instead of arguing a level:

```bash
cd <frontier-infra>/the-machine && python -m kit score <…>/machine-driver
```

It scores machine-driver at **L2 Instrumented (strict)** against the current spec. machine-driver
enforces the L3 controls (a non-bypassable gate, budget governor, terminal quarantine, escalation,
fail-closed verify) **and now emits a per-task Ed25519-signed AAR** — so the **Receipted/L4 receipt
obligation is met**. But the overall level stays **gated at L3** because **vNext** raised the L3 bar
to also require Δ1 reversibility, Δ2 operator-override, and Δ3 runtime-health. **Δ1 (reversibility
as a term in the gate) and the Box-0 commit hard-deny are now built and tested** (kit: both rows
PASS; `tests/test_gate.py` proves commit / propose / hard-deny against a real git repo); Δ2
operator-override and Δ3 runtime-health remain. Honest reading: receipts are L4-ready and two L3
gate rows now PASS; the level stays **L2** until the remaining L3 rows clear.

## The six boxes → the code

| Box | Obligation | In `driver.py` | Status |
|---|---|---|---|
| **0 Contracted Decomposition** | no move begins without a contract whose target is ratified by ≠ its proposer | a commit is **HARD-DENIED** unless `ratified_by != proposed_by` — refused at the `apply_mutation` chokepoint, not a silent downgrade; propose runs (no durable mutation) are unaffected | **Enforced** (Council ratifier wired manually) |
| **1 Durable Goal + State** | goal/state outlive the session; resume the same move after `kill -9` | `goal.json` + atomic `.tmp`→replace; `runs` persisted; restart resumes pending tasks | **Pass** |
| **2 Dumb Driver** | control loop spends **zero model tokens** | the `while` loop is pure deterministic plumbing; the only judgment is the `worker_cmd` shell-out | **Pass** |
| **3 Fresh Workers** | each move = a fresh, bounded context | one new subprocess per task; no reused transcript | **Pass** |
| **4 Verify vs Reality** | no durable mutation without independent, ground-truth PASS; `verifier ≠ subject` | `verify_cmd` exit code is ground truth; runner independent of the worker; no verifier ⇒ trust 0 ⇒ cannot pass; **per-task Ed25519-signed AAR** emitted via `aar.mjs` | **Enforced** + **Receipted** (signed AAR → AAR-L2) |
| **5 Autonomy Dial** | `effective = min(operator, ceiling, verifier_trust)`; no verifier ⇒ propose-only; reversibility-aware; non-bypassable gate | `effective_mode(state, task)` with a **per-task reversibility term** (irreversible actions must clear `contract.irreversible_min_trust`, strictest if unset); mutation routed through the single `apply_mutation` chokepoint; **budget governor** halts on breach | **Enforced** + **Δ1 reversibility**; vNext Δ2 operator-override **not yet** |

## Cross-cutting obligations

| Obligation | Status |
|---|---|
| **AAR receipts** | per-task **Ed25519-signed** AAR via `aar.mjs` → `aar/<id>.json`; self-verifies to **AAR-L2** (`verifier ≠ subject`, ground-truth, evidence hash). **Pass** |
| **Idempotency** | key recorded per task (`idem_key`); store-level dedup **pending** (lands with a real board/gateway) |
| **Quarantine** | terminal `blocked` after `max_attempts` — surfaced, not re-queued. **Pass** |
| **Escalation (with alert)** | block / quarantine / budget-halt → Telegram operator alert. **Pass** |
| **Cost / Resource Governor** | `budget.max_worker_runs` / `max_wall_seconds` → HALT + alert. **Pass** — the control the 131-duplicate incident lacked |
| **Observability** | `last_success_at` heartbeat + hash-chained loop log (`driver-log.jsonl`). **Pass**; vNext Δ3 independent-staleness-monitor **not yet** |

## Two artifacts (iteration 2, 2026-06-14)

- **`driver-log.jsonl`** — the hash-chained loop **audit** (dispatch / requeue / verify / halt). Telemetry.
- **`aar/<task-id>.json`** — one canonical **Ed25519-signed AAR** per resolved task. The proof layer.

Configured by `goal.json` keys `aar_tool` · `aar_priv` · `subject` · `principal`. Absent ⇒ the driver
runs **keyless** (skips the AAR; still writes `driver-log.jsonl`). The signing **identity + key** is the
one operator-gated step: `node aar.mjs keygen --did did:web:<domain>:machine-driver …`, then publish
`did.json`. The worker is replaceable; the principal is not.

## Evidence (run it, don't trust it)

```bash
python3 -m unittest discover -s tests -v   # 6 tests
```

Demonstrated: **budget-breach → halt + alert** · **missing-verifier → propose-only** ·
**max-attempts → quarantine** · **hash-chain integrity** · **verified task → signed AAR that
independently verifies to AAR-L2** · **keyless run → AAR skipped, loop audit still written**.

## What's left for The Machine L3/L4 (vNext)

- **Δ1 reversibility gate** (Box 5), **Δ2 operator-override** (Governance), **Δ3 runtime-health**
  monitor — the gates vNext added; the kit flags each.
- **idempotency enforcement** against a real board/gateway; **lying-worker → caught** and
  **gate-bypass** negative tests (chaos/replay, not yet run).
