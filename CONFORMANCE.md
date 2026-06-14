# machine-driver Conformance

machine-driver is a reference implementation of **The Machine** — the six-box harness for
reliable long-running applied intelligence. The standard it conforms to is *The Machine —
Conformance Spec v1*; this file is the honest self-assessment: what's **enforced today** vs.
**declared / planned**. The spec describes; this file falsifies.

## Current level: **L3 — Enforced** (heading to L4 — Receipted)

The Machine's conformance ladder: L0 Look-Alike · L1 Declared · L2 Instrumented · **L3 Enforced**
· L4 Receipted · L5 Trusted Autonomy. machine-driver **blocks, caps, alerts, and escalates per
contract** (L3). It is **not yet L4** — that requires signed AAR receipts on material actions
(iteration 2, below).

## The six boxes → the code

| Box | Obligation | In `driver.py` | Status |
|---|---|---|---|
| **0 Contracted Decomposition** | no move begins without a contract whose target is ratified by ≠ its proposer | loads `contract`; `commit` blocked unless `ratified_by != proposed_by`; missing/self-ratified ⇒ alert + propose-only | **Enforced** (Council ratifier wired manually) |
| **1 Durable Goal + State** | goal/state outlive the session; resume the same move after `kill -9` | `goal.json` + atomic `.tmp`→replace; `runs` persisted; restart resumes pending tasks | **Pass** |
| **2 Dumb Driver** | control loop spends **zero model tokens** | the `while` loop is pure deterministic plumbing; the only judgment is the `worker_cmd` shell-out | **Pass** |
| **3 Fresh Workers** | each move = a fresh, bounded context | one new subprocess per task; no reused transcript | **Pass** |
| **4 Verify vs Reality** | no durable mutation without independent, ground-truth PASS; `verifier ≠ subject` | `verify_cmd` exit code is ground truth; the test runner is independent of the worker; no verifier ⇒ trust 0 ⇒ cannot pass | **Enforced**; signed receipt **pending (L4)** |
| **5 Autonomy Dial** | `effective = min(operator, ceiling, verifier_trust)`; no verifier ⇒ propose-only; non-bypassable gate | `effective_mode()`; mutation only via the gate; **budget governor** halts on breach | **Enforced** |

## Cross-cutting obligations

| Obligation | Status |
|---|---|
| **Idempotency** | key recorded per task (`idem_key`); store-level dedup **pending** (lands with a real board/gateway) |
| **Quarantine** | terminal `blocked` after `max_attempts` — surfaced, not re-queued | **Pass** |
| **Escalation (with alert)** | block / quarantine / budget-halt → Telegram operator alert | **Pass** |
| **Cost / Resource Governor** | `budget.max_worker_runs` / `max_wall_seconds` → HALT + alert | **Pass** — the control the 131-duplicate incident lacked |
| **Observability** | `last_success_at` heartbeat + hash-chained loop log | **Pass** |
| **AAR receipts** | per-task signed proof | **Pending (iteration 2)** |

## Evidence (run it, don't trust it)

```bash
python3 -m unittest discover -s tests -v
```

Demonstrated today: **budget-breach → halt + alert** · **missing-verifier → propose-only** ·
**max-attempts → quarantine** · **hash-chain integrity**. Pending evidence for L4:
**lying-worker → caught** and **duplicate-key → single-effect** both need the signed-AAR +
idempotency-enforcement work, and a **gate-bypass negative test**.

## Iteration 2 → L4 (Receipted)

Emit a canonical, **Ed25519-signed AAR** per verified/contradicted task by shelling out to the
org's own signer (`agentcontrolplane/tools/aar.mjs`). The verify step already produces
**L2-shaped** material (`verifier.id ≠ subject`, ground-truth-against-reality, evidence hash);
iteration 2 shapes + signs it. Mirror how Conductor already emits AAR so the siblings match.
See `README.md` → *Iteration 2*.
