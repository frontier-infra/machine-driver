# machine-driver

The **dumb, tireless driver** — **Box 2 of The Machine** (the deterministic control plane), the piece that was missing for *code* work. It is the deliberately-stupid loop that keeps work moving without a human pressing the button. It spends **zero model tokens**; the model only runs inside the worker step.

Part of **[Frontier Infra](https://github.com/frontier-infra)** — sibling to **[AVL](https://github.com/frontier-infra/avl)** (view), **[AAR](https://github.com/frontier-infra/agentcontrolplane)** (proof), and **[Conductor](https://github.com/frontier-infra/conductor)** (the ops driver). machine-driver is the *code-work* driver: the same dumb-loop shape as Conductor — checkpoint/resume, per-task isolation, verify-by-result — pointed at repos instead of a help-desk queue.

> Conformance to the six-box spec: see [`CONFORMANCE.md`](./CONFORMANCE.md) · contributing/agents: [`AGENTS.md`](./AGENTS.md).

## The 5-part loop (all in `driver.py`)
1. **State** — `goal.json` on disk. The goal never lives only in a context window.
2. **Driver** — the `while` loop. Deterministic, no judgment, runs for days.
3. **Worker** — one fresh process per task (`worker_cmd`). The *only* place judgment is spent.
4. **Verify** — `verify_cmd` exit code is ground truth. Pass → done. Fail → re-queue (fresh attempt) → **block and surface** after `max_attempts`. It cannot silently skip.
5. **Autonomy** — `mode`: `"propose"` (leave the diff for review) or `"commit"` (proceed). Fail-closed to propose.

## Run it for real
1. Copy `goal.example.json` → `goal.json`. Point `repo` at a real repo.
2. Set `worker_cmd` to your coding agent, e.g. `claude -p "{task}" --permission-mode acceptEdits`.
3. Set `verify_cmd` to your ground truth, e.g. `npm test` (or the goal-contract gate, or `cargo test`, etc.).
4. Break the goal into a few small `tasks` (each one a fresh-context burst).
5. `python3 driver.py goal.json`. Watch it take a step, get verified, take the next.

Start in `"propose"`. Turn the dial to `"commit"` one goal at a time, as the verifier earns your trust. That is Part 5 — a setting, not a rebuild.

## Box 0 contract + budget (iteration 1, 2026-06-14)
`goal.json` now carries two extra blocks, both deterministic, neither adds a model token:
- **`contract`** (Box 0) — `definition_of_done`, `acceptance_tests`, `immutable`, `autonomy_ceiling`, `proposed_by`, `ratified_by`. The driver refuses to commit unless the contract is **independently ratified** (`ratified_by != proposed_by`) — the Council is the natural ratifier of the *target*; ground-truth tests stay the verifier. `autonomy_ceiling`: `0`=propose, `1+`=commit-allowed.
- **`budget`** (Box 5 governor) — `max_worker_runs` / `max_wall_seconds`. Breach → **HALT + operator alert**. This is the control the 131-duplicate incident lacked.

Every transition appends a hash-chained receipt to **`aar.jsonl`** — this is the **driver-loop audit trail** (a tamper-evident *sequence* of dispatch/requeue/halt), **not yet a canonical signed AAR**; see *Iteration 2*. Block / quarantine / budget-halt fire a **Telegram alert** (`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`), never a silent stdout line.

## Iteration 2 (planned) — canonical signed AAR (eat our own cooking)
The hash-chain in `aar.jsonl` is loop telemetry, **not** the proof layer. The real proof is
the org's own standard: a per-task, **Ed25519-signed** Agent Attestation Record
(`../agentcontrolplane`, agentscontrolplane.org). Because AAR is *our* standard, the driver
**must** emit it — that's the conformance proof, not a nicety.

**Two artifacts, split cleanly:**
- `driver-log.jsonl` — rename of `aar.jsonl`; the hash-chained loop audit (dispatch / requeue / halt). Stays.
- `aar/<task-id>.json` — NEW: one canonical signed AAR per verified/contradicted task.

**The verify step already produces L2-shaped material** — `verifier ≠ worker` for free:

| AAR field | from the driver |
|---|---|
| `aar` | `"0.02"` |
| `subject` | the worker — `did:web:<org>:machine-driver` |
| `principal` | the signing org — `did:web:<org>` (= `sig.by`) |
| `task` | `{ "id": task.id, "claim": task.goal }` |
| `verdict` | `"verified"` (verify exit 0) · `"rejected"` (non-zero) |
| `ground_truth` | `"confirmed"` (exit 0) · `"contradicted"` (non-zero) |
| `reason` | one line, e.g. `"verify_cmd exited 0 against repo HEAD"` |
| `checks` | `[{ source: repo, query: verify_cmd, observed_at: now, response_sha256: sha256(verify_output), excerpt: tail }]` |
| `verifier` | `{ id: did:web:<org>:<verifier>, independence: "same_principal" }` — `id != subject` ⇒ **L2** |
| `issued` | `now()` |

**Integration (driver stays pure-Python; shell out to our own signer):**
1. Build the record from the verify result → write `aar/<task-id>.json`.
2. `node ../agentcontrolplane/tools/aar.mjs sign aar/<task-id>.json --priv <key>` → adds `sig` (Ed25519, JCS-canonical, `sig.by = principal`).
3. self-test: `node ../agentcontrolplane/tools/aar.mjs verify aar/<task-id>.json` → expect `→ conformance: L2`.

New `goal.json` keys: `aar_tool` (path to `aar.mjs`), `aar_priv` (key path), `subject`, `principal`. If absent ⇒ skip AAR (still write `driver-log.jsonl`), so the driver runs keyless.

**The one human-gated input (irreducibly yours): the signing identity + key.**
`node ../agentcontrolplane/tools/aar.mjs keygen --did did:web:titaniumcomputing.com:machine-driver --out-priv secrets/machine-driver.jwk.json --out-did <domain>/.well-known/did.json`, then publish `did.json`. *The worker is replaceable; the principal is not.*

**Conformance target:** L2 now (signed + independent verifier + committed evidence); L3 (transparency log) later. Mirror how **Conductor** already emits AAR so the two siblings match.

## Status / next (when fresh)
Iteration-1 hardened + smoke-tested (happy path emits AAR · governor halts + alerts · no-verifier ⇒ propose-only). To reach Level 3–4 conformance next:
- **canonical signed AAR** — see *Iteration 2* above: shape per-task records + sign via our own `aar.mjs` → L2;
- **Council = Box-0 ratifier** — wire `roundtable.sh` to draft/ratify the contract before the first real run;
- **idempotency enforcement** against a real board/gateway (reject the duplicate ACTIVE row, not just record the key);
- feed `tasks` from **GitHub Issues / the ArgentOS board** instead of a hand-written list;
- keep task *selection* dumb (rules table only if ordering ever needs it — never a model in the loop).
