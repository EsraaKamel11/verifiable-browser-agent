# A verifiable, self-healing browser agent

**Design spec. Written 2026-08-17. Nothing in this document is built yet.**

Every claim below is a design decision, not a report of working code. The Designed-vs-Built line is enforced throughout: this file describes what will be built and why. When code exists, a separate document reports what it does.

---

## 1. What this is

A browser agent that automates an authenticated web workflow and **can prove whether the work actually happened**, against a system of record rather than against the page that claims success.

It is designed against a simulated payer-enrollment portal with a planted silent-failure case, three layout variants, and an acceptance rubric of eight requirements. Both the simulation and the agent are authored here. Section 10 states what that costs the artifact's evidentiary value and how it is mitigated.

### The thesis, in one line

**Every judgment that could be handed to a language model and is instead handed to an oracle is a place the system cannot lie.**

The agent has exactly one LLM role. Verification, tier enforcement, retry decisions, memory promotion, and pass/fail scoring are all deterministic.

### The five demonstrations

1. A provider whose enrollment shows on-screen success and never posts is caught and reported as not enrolled.
2. The portal's layout changes twice; the agent completes both times with no code edit.
3. The same rubric runs with memory off and memory on: same verdicts, lower cost.
4. Given a bare goal with no oracle, the agent refuses the task and names what it needs.
5. With the portal down mid-submit, the agent proves nothing posted and escalates, rather than reporting success.

---

## 2. The problem being solved

Three failures define the target, and each maps to a mechanism:

| Failure | Mechanism |
|---|---|
| The page said "Submitted successfully"; the payer had rejected it | Cross-system verification; the verdict is derived from the record, never from the page |
| The portal changes every few weeks and hardcoded selectors break | Intent-keyed resolution against a live enumerated element set |
| A retry after a timeout created a duplicate | Never retry an irreversible act on ambiguity; ask the record first |

A fourth requirement sits on top: the system should get **cheaper** to run over time, not more brittle. That is what the memory layer is for, and it is the requirement most easily satisfied dishonestly.

---

## 3. Architecture

### 3.1 Six concerns

```
CONTRACT      declares intent, tier, and the oracle          (authoring time)
PERCEPTION    enumerated element set; never a raw viewport coordinate
EXECUTION     acts through one choke point; consumes verdicts, never produces them
VERIFICATION  page-verify steers the loop; system-verify alone decides outcomes
MEMORY        removes re-reasoning; never removes verification
GUARD         tier enforcement in code, at the choke point
```

Verification is a first-class concern rather than a clause inside execution. The two critical requirements are both verification requirements, and the executor is deliberately unable to mint a verdict.

### 3.2 Process split

| Runs where | What |
|---|---|
| Python harness | contract load, the loop, both verifies, memory, audit, retry adjudication |
| Agent SDK session | **only** when a step needs resolving: memory miss, or a failure needing a new path |
| Playwright | behind an in-process MCP server exposing the enumerated action space |

The SDK is an LLM loop with no execute-without-reasoning mode. If it drove every step, every run would cost full tokens and "cheaper over time" would be unachievable by construction. **A memory hit spawns zero SDK sessions.** The memory layer's job is to make the reasoner unnecessary.

Fresh session per resolution is chosen over one long-lived streaming session: observations dominate history and would be re-sent every turn; compaction can drop rules mid-run; a confused resolution would contaminate every later one; and, decisively, per-resolution determinism is what makes each resolution a replayable `(intent, observation) -> Action` fixture. Eval-gated promotion depends on that.

Resolution sessions are **top-level harness-spawned sessions**, not SDK subagents. The distinction matters: SDK subagents are in-session delegates and inherit budget accounting that does not apply here.

### 3.3 Modules

| Module | Job |
|---|---|
| `contract/` | schema, loader, acceptance gate |
| `perceive/` | enumerated element set; shadow and light DOM traversal; topmost filtering; epoch stamping |
| `act/` | in-process MCP server; **the single action-execution choke point** |
| `verify/` | `page_verify` (steers) and `system_verify` (decides); separate types, separate paths |
| `resolve/` | two entry points: `resolve_step`, `resolve_failure` |
| `memory/` | learned fixes: lookup, drift detection, capture, supersede |
| `guard/` | tier gate, credential injection, PII redaction |
| `run/` | the state machine, retry budgets, circuit breaker, outcome taxonomy, escalation routing |
| `audit/` | append-only hash-chained evidence |
| `report/` | the human-readable per-enrollment record |
| `evals/` | the three test tiers |

`run/` exists so the state machine has a named home rather than growing inside an unnamed harness. `audit/` and `report/` are split because machine evidence and a human-readable record are different artifacts for different readers.

### 3.4 One agent, deliberately

One LLM role: resolve a page into a typed action. Two scopes: resolving a step, and resolving a failure.

Everything else that could be an agent is not:

| Candidate | Built as | Why |
|---|---|---|
| Promoting a learned fix | deterministic replay + oracle check | a resolver certifying its own fix is self-certification |
| The enrollment verdict | derived, unassertable | there is no channel to claim success |
| Tier enforcement | a function at the choke point | guardrails in code, not in prompt |
| Retry adjudication | an oracle read | a count, not an opinion |
| Pass/fail scoring | deterministic predicates | no LLM judge anywhere |

**This is a coordinator/worker system**: a deterministic coordinator delegating to LLM workers and integrating their output through an oracle. Stated in those words because the shape is conventional even though the agent count is low.

**Considered and rejected: multi-lens fan-out.** Spawning several resolvers with different resolution lenses and taking the first that verifies is coherent on paper. Rejected: proposals cannot be oracle-adjudicated without executing them, side-effecting candidates cannot be tried in parallel, and in this world a second consecutive resolution failure is almost always environmental rather than perceptual, so the path would ship as dead code.

The real concurrency exhibit is **provider-level**: independent identity scopes, bounded parallel sessions, one shared oracle, one browser context per provider.

---

## 4. The contract

### 4.1 Promptable at authoring time, contracted at run time

Free text enters at compile time. A **TaskContract** is authored offline and human-reviewed; run time is a general resolver executing it.

This resolves a real tension. Verification requires knowing what "done" means and having an oracle to check it. An oracle is domain-specific. A fully general run-time-promptable agent has no oracle and structurally cannot do the one thing this project is about.

```yaml
contract: payer_enrollment
version: 3
site: enrollment_portal
goal: "Enroll a provider with their payer and confirm it posted to the payer's records."

oracle:
  kind: http_json
  url: "{base}/api/sor/enrollment/{npi}"
  strength: cross_system            # cross_system | on_page | none

identity:
  key: [npi, payer]
  resolve_ambiguity_by: oracle      # never by retry

steps:
  - step_key: portal.login
    intent: "sign in with the team credentials"
    tier: 2
    credentials: {ref: portal, fields: [email, password]}
  - step_key: portal.verify_2fa
    intent: "enter the authenticator code and confirm the not-a-robot checkbox"
    tier: 2
    credentials: {ref: portal, fields: [otp]}
  - step_key: provider.open
    intent: "open the record for provider {npi}"
    tier: 1
  - step_key: enrollment.select_payer
    intent: "select the payer named in the contract"
    tier: 2
  - step_key: enrollment.submit
    intent: "file this enrollment with the payer"
    tier: 3
    preconditions: [oracle.baseline_read]
    satisfied_when: oracle.confirmed
    postconditions:                 # deterministic page_verify predicates
      - text_present: "Submitted successfully"
      - text_absent: "Please confirm you have reviewed"

pii:
  redact: [password, otp]
  never_screenshot_urls: ["/", "/login", "/verify"]
```

**`intent`, not selector.** The submit control is renamed and moved in every layout variant. "File this enrollment with the payer" resolves against all of them. The selector is a cached resolution that must revalidate; the intent is the durable key.

**`postconditions` make page-verify deterministic.** If page-verify needed a model call, the memory-hit path would spawn an LLM session every step and the cost claim would be false.

### 4.2 The acceptance gate

Runs before any browser opens. Refusal is a first-class outcome. The transcript below is illustrative.

| Contract state | Autonomy granted |
|---|---|
| `strength: cross_system` | tiers 1, 2, 3 |
| `strength: on_page` | tiers 1, 2; tier 3 propose-only, human approves |
| `strength: none`, or absent | tier 1 only; tiers 2 and 3 refused at intake |
| oracle declared but unreachable at start | refuse to start |

```
$ agent run --goal "enroll provider 1700000001 with the payer"
REFUSED. This is a tier-3 act with no oracle binding.
I can perceive the portal and report what I see (tier 1).
To submit, I need a source of truth that confirms it posted.
Supply a contract with an oracle binding, or the reconciliation endpoint.
```

This is the honest generalization of "cannot confirm" from run time to task acceptance: the agent declines work it could not report on truthfully.

### 4.3 Tier enforcement

A generic click tool is tier-opaque: opening a record and filing an enrollment look identical to a hook. The action space is shaped so the question cannot arise.

```python
class Action(TypedDict):
    kind: Literal["click","fill","select","hover","scroll","navigate",
                  "submit","extract","draw"]
    target_id: int          # index into the enumerated set; never a raw selector
    value: str | None
    step_key: str           # tier comes from the contract via this
    epoch: int              # observation epoch; stale ids are refused
```

Three enforcement points, deliberately redundant:

1. **Tool grant.** Tier-3 tools are absent from the allowed set unless the current step is tier-3 with preconditions met. Forced tool selection does not exist in this runtime, so non-exposure is the only available lever.
2. **The choke point.** `guard.check()` is called inside `execute()`, the only path to a side effect. Memory-originated and resolver-originated actions traverse identical enforcement and emit identical evidence.
3. **Harness postcondition.** After any tier-3 act, `system_verify` runs whether or not the model asked. **The oracle is not a tool.** If it were, the model could decline to call it.

**The shaping rule.** An action on a submit-type control is classified `kind: submit` at the choke point from element metadata, regardless of what the resolver called it, and is refused unless the current step is tier-3 with a live baseline handle. The propose-only path in 4.2 has no cross-system baseline to hold and is not built; if it were, an approved act would need an explicit exemption rather than an implicit one. Without this rule a plain click during a lower-tier step could fire the form and post an unbaselined record.

**Epoch re-binding.** Stored actions carry a stored identity, not a target id. At execution the identity is re-bound to a `target_id` in the current epoch; an id from a stale epoch is refused rather than translated silently.

A hook on SDK tool calls is defense-in-depth for resolver sessions only. It cannot be the gate, because the memory path does not call the SDK at all.

### 4.4 Credentials

The model emits `{kind: "fill", target_id: 7, field: "password"}` and never sees the value; the guard injects it browser-side. Screenshot capture is suppressed for the URLs named in `never_screenshot_urls`.

Two honest limits, stated rather than inherited silently:

- The **login email is not masked**. It appears in observations. Only the password and OTP are protected.
- The OTP field in the target world is **not a password input**, so any observation of the auth page after fill contains it in cleartext.

**Therefore the guard scrubs injected literals from every outbound payload**, not only from screenshots: it knows every value it injected and removes those literals from observations sent to a model and from audit records before they are written. Scrubbing respects token boundaries, so a confirmation number that happens to embed a code's digits is not corrupted. Screenshot suppression alone is insufficient, because the loop re-perceives between the actions of a sequence and a post-fill observation of the auth page is guaranteed.

**Provider data is a separate question and is not solved by the above.** Provider names and identifiers necessarily transit the model API on every cold resolution: they are the content of the page being resolved. The redaction list covers credentials only. One genuine mitigation falls out of the architecture rather than from a policy: a memory hit spawns no model session, so a step served from memory keeps provider data out of the model provider entirely. The honest counterweight: one cold resolution of an index step ships every listed provider's details, not only the one being worked on.

---

## 5. The loop

### 5.1 Control flow

```python
def run_step(step: Step, ctx: RunContext) -> StepOutcome:
    obs = perceive(ctx)                                # enumerated, epoch-stamped
    fix = memory.lookup(ctx.site, ctx.contract, step.step_key)   # by step_key
    if fix and fix.fingerprint != obs.fingerprint:
        audit.stale_fix_detected(fix, obs)             # supersede-on-drift evidence
        fix = None
    baseline = oracle.read(ctx) if step.satisfied_when else None   # BEFORE any action

    if fix and fix.still_resolves(obs):
        driver, source = replay(fix, ctx.bindings), f"memory:{fix.fix_id}"
    else:
        driver, source = resolve_session(step, obs, ctx), "cold"

    page = drive(driver, step, ctx)                    # every act via the choke point

    if step.satisfied_when:
        return system_verify(step, ctx, baseline, page)   # decides, always
    return StepOutcome(page, verif_strength="on_page", source=source)
```

`run_step` returns a typed outcome and **never calls resolution inline**. `run/` owns re-entry, the retry budget, and the circuit breaker, so cross-invocation state is not hidden inside a recursive call.

**`drive()` is one execution model for both paths.** Both branches yield a *driver*, never a finished plan: `replay` yields the stored sequence with parameters bound to this invocation, and `resolve_session` yields a live session that emits one action at a time. `drive` pulls actions from either, sends each through the choke point, and re-perceives and settles between them. That observation sequence is what 6.3 slices for capture. Neither branch returns a bulk plan, because tool-grant enforcement and capture-slicing both require turn-by-turn acting.

The baseline read precedes the branch, so it happens before any action on either path. Gating it on `satisfied_when` rather than on the tier keeps the read and its use under one condition; the contract schema requires every tier-3 step to declare `satisfied_when`, so the tier-3 predicate's baseline requirement cannot be met by a step that never reads one.

`system_verify` takes the page verdict as an argument because several of its outcomes are joint: the same unchanged count means DISCREPANCY after a page success, REJECTED after a stated refusal, and VERIFIED-NOT-DONE after an infrastructural failure.

Memory lookup is **by `step_key`, then fingerprints are compared**. Keying the lookup on the fingerprint would make a stale fix a silent miss, indistinguishable in the audit from having no memory at all.

### 5.2 Page failure has three categories

Collapsing these into one is how a portal outage becomes a resubmit.

| Category | Example | Route |
|---|---|---|
| Mechanical | click did not land, element gone | resolve a new path |
| Rejected | business refused with a stated reason, before any record write | resolve with the refusal text as context; write a negative memory entry |
| Infrastructural | 5xx, navigation timeout | **never** resolve; classify via the oracle |

**For any step with `satisfied_when`, page-FAILED routes to `system_verify` first.** Resolution is entered only with an oracle verdict in hand. Without this, a portal outage sends the agent into a resolution spiral against a 503 page, and a submit whose response was lost gets retried.

### 5.3 System verification: delta-based

Every outcome is computed against the pre-act baseline. Absolute predicates would confirm an enrollment the agent never made if a row already existed.

| Oracle delta | Outcome | Next |
|---|---|---|
| `count == baseline + 1`, identity matches, confirmation matches | CONFIRMED | next step |
| `count == baseline`, page claimed success | **DISCREPANCY** | escalate this provider; never resolve, never resubmit |
| `count == baseline + 1`, **identity does not match** | **MISFILED** | stop this provider; escalate; the act posted, but not the act the contract asked for |
| `count > baseline + 1` | DUPLICATED | stop everything; invariant tripwire |
| `count == baseline`, page failed mechanically | NOT ACTED | resolution permitted; nothing was filed |
| `count == baseline`, page rejected with a stated reason | REJECTED | resolution permitted with the refusal text; write a negative entry |
| `count == baseline`, page failed infrastructurally | VERIFIED-NOT-DONE | retry permitted; still escalate |
| oracle unreachable | UNVERIFIABLE | escalate; **never** resubmit |
| baseline already enrolled | ALREADY_SATISFIED | never submit |

CONFIRMED requires three agreements: the count incremented by one, the recorded identity matches the contract, and **the confirmation number on the page appears in the record**. The third catches a page that mints a confirmation number corresponding to nothing.

MISFILED is the outcome a naive design cannot name: a record was created, so the count moved, but its identity does not match what the contract asked for.

**A per-entity oracle read cannot see a record filed under the wrong entity**, because it only asks about the entity the contract meant. So on DISCREPANCY the agent reconciles against the **whole table** before concluding: if the confirmation number shown on the page appears under a different entity, the outcome is MISFILED rather than DISCREPANCY, and the misfiled act is visible at act time instead of surfacing only in a post-run sweep. Stated plainly: the per-entity delta catches a wrong *value* under the right entity; the table reconciliation is what catches a wrong *entity*.

DISCREPANCY and MISFILED stop one provider; the rest of the batch proceeds. DUPLICATED stops everything, because under a fresh baseline and a single writer it can only arise from a guard defect.

### 5.4 Three absences, three verdicts

| Signal | Meaning | Verdict |
|---|---|---|
| oracle answered, count unchanged | verified absent | NOT ENROLLED |
| oracle unreachable | unknown | UNVERIFIABLE |
| identifier absent from the portal | wrong question | INVALID |

INVALID is adjudicated by the portal, not the oracle: the reconciliation endpoint answers "not enrolled" for any identifier, including ones that do not exist.

### 5.5 Ambiguity and restart

**Never retry an irreversible act on ambiguity.** Read the oracle: delta of one means it landed; delta of zero means a retry is permitted. If the oracle is also unreachable, the outcome is UNVERIFIABLE and the run escalates.

**Why not an idempotency key.** The source design passes one with every side-effecting act. A browser driving a rendered form cannot: the form carries no such field and a form post cannot set a header, and reaching past the form to inject one would bypass the enumerated action space that the whole safety argument rests on. Identity is therefore enforced by asking the record instead, which the target rubric explicitly permits.

The same invariant covers a crash between acting and verifying, which requires the baseline and the intent-to-act to be written **atomically** before the act. Restart is a decision made from the record, never a blind resume.

---

## 6. Memory

### 6.1 Schema

```sql
CREATE TABLE learned_fix (
  fix_id            UUID PRIMARY KEY,
  site              TEXT NOT NULL,
  contract          TEXT NOT NULL,
  step_key          TEXT NOT NULL,
  intent            TEXT NOT NULL,        -- the durable key
  page_fingerprint  TEXT NOT NULL,        -- structural; see 6.2
  resolved_actions  JSONB NOT NULL,       -- ORDERED LIST, not one action
  match_mode        TEXT NOT NULL,        -- exact_identity | structural; exact_identity required at tier 3
  action_tier       INT  NOT NULL,        -- max tier across the sequence
  polarity          TEXT NOT NULL DEFAULT 'positive',
  failure_mode      TEXT,
  verif_strength    TEXT NOT NULL,
  trials            INT  NOT NULL DEFAULT 0,
  successes         INT  NOT NULL DEFAULT 0,
  confidence        REAL NOT NULL DEFAULT 0,   -- ranking and reporting only
  provenance        TEXT NOT NULL,             -- candidate | eval_promoted
  valid_from        TIMESTAMPTZ NOT NULL,
  valid_to          TIMESTAMPTZ,               -- NULL = current
  recorded_at       TIMESTAMPTZ NOT NULL,
  last_used_at      TIMESTAMPTZ
);

CREATE UNIQUE INDEX one_current_positive_fix_per_step
  ON learned_fix (site, contract, step_key)
  WHERE valid_to IS NULL AND polarity = 'positive';
```

The index is scoped to positive polarity so a step can carry one current fix alongside several current negative entries.

**`resolved_actions` is a list** because a real fix can be a sequence: ticking a newly-required checkbox and then submitting is one step with two actions. `still_resolves` is an AND over every element; `action_tier` is the maximum; the sequence re-perceives between actions so each target id is epoch-fresh and each action passes the choke point individually.

**Stored actions carry parameter references, not literals.** The fix records the parameter bindings of the invocation it was captured under, and **every stored string is templated by substring against those bindings**, not by equality: identity components, values, and URLs alike. A dashboard link whose accessible name reads `1700000001 - Dr. Maria Santos (Family Medicine)` is stored as `{npi} - Dr. Maria Santos (Family Medicine)`; a payer selection is stored as `value: "{payer}"`.

**Equality is the wrong test and it fails dangerously.** A target identity that *contains* a parameter alongside unrelated text is equal to nothing, so equality would store it whole. In a portal whose index lists every entity on every visit, that literal identity then re-binds **successfully, to the wrong entity**: the fingerprint matches because the index is entity-invariant, the identity check passes because the stored element really is on the page, and the guard permits it because opening a record is a read. Every later step then operates on the wrong record, and the oracle read for the entity the contract *meant* cannot see it.

**At reuse, bind first, then require an exact match of the bound identity.** After binding, the residual literal is what protects the act: `{npi} - Dr. Maria Santos (Family Medicine)` bound for a different provider yields a string that matches no element, so the lookup misses and resolution runs cold. Substring templating therefore fails safe in both directions. A false positive, where static page text coincidentally contains a parameter value, binds the current value and at worst causes a miss. It can never produce a wrong act.

**Consequence, accepted and stated:** an identity that carries unremovable entity-specific text can never be reused across entities. Where an intent is parameterized, resolution prefers a target that templates completely: navigating to a templated path reuses perfectly, where clicking an index entry cannot. The zero-sessions claim in 7.2 is therefore **per step**, not per run, and the spec does not claim a warm run spawns no sessions at all.

The credential fill already works this way, referring to a field rather than carrying a secret. This generalizes that rule to every parameter the contract names.

**`match_mode = exact_identity` is required at tier 3**, forbidding structural or positional selectors. A positional selector can resolve to the wrong control after a redesign and submit before any verification runs. It can also resolve to the *right* control by luck, which silently demonstrates the blind replay the design exists to prevent.

### 6.2 The fingerprint

A whole-DOM hash is unusable: the page interpolates per-provider content, so one layout would mint one fingerprint per provider and no fix would ever be reused.

```
fingerprint = sha256(
    contract, step_key,
    normalize_url(url),      # path parameters templated out
    form_signature,          # templated action, method,
                             # ordered (tag, name-attribute, type) for named controls,
                             # plus (id, text) for buttons
    control_set,             # presence, not selection state
)
```

Built from **attribute-level data, not accessible names**. Name attributes are value-free and provider-invariant; an accessible name can absorb a field's value when a control sits inside its own label, which would reintroduce per-provider drift through the back door. Buttons must be included by id and text, or two layouts whose named inputs are identical collapse into one fingerprint.

Selection state is excluded: which option is currently selected is state, not structure. Frame presence is excluded: it does no discriminative work here and a script-attached shadow root can be absent at snapshot time, producing spurious misses.

Accessible-name comparison is reserved for `exact_identity` enforcement at act time, where a stored name is either static or fully templated and bound before comparison.

**Validated (2026-08-20) against the real snapshot tool.** See `docs/findings/2026-08-20-accname-probe.md`: on the vendored world's provider record page (Playwright 1.49.0, Chromium), the NPI field -- an embedded, read-only input inside its own `<label>NPI <input .../></label>` -- produced an accessibility-tree node with `name: "NPI "` and a separate `value: "1700000001"`. The name did not absorb the value. Names do not absorb values in this snapshot tool, so the constraint relaxes: it is no longer load-bearing. The attribute-level fingerprint is still built as specced, because it remains more robust regardless (buttons still need id+text to disambiguate layouts whose named inputs are otherwise identical), but its justification is now prudence, not necessity.

### 6.3 Capture

> **Capture the action suffix beginning at the start of the final contiguous run of observations whose fingerprint matches the step's entry state, through the confirming action.**

Formally: find the latest observation whose fingerprint does **not** match entry, and capture from the first matching observation after it.

The naive version -- "from the last matching observation" -- captures too little. Because the fingerprint excludes control state, the observation after ticking a checkbox still matches the entry fingerprint, so the captured fix would omit the tick and fail on every replay. Memory would miss forever while appearing healthy.

The failed approach is written as a **negative entry** at the same moment, so other entities do not rediscover the same rejection.

**Negative entries have a read path, or they are pointless.** Before a resolution session is spawned for a step, current negative entries matching that step are injected into its context as approaches already known to fail, with the refusal text. An entry is superseded if the same approach later succeeds, so a portal fix cannot leave a permanent blinder. Decay of negative entries is out of scope here and named as such.

**Stated limit:** suffix-slicing is sound only because re-entering the record page re-renders form state from scratch. In a portal where entry state does not reset form state, the rule would drop required earlier actions.

### 6.4 Promotion

```
CONFIRMED -> candidate (never pre-applied)
          -> eval gate: deterministic replay + oracle check
          -> pass: eval_promoted (pre-appliable)
          -> fail: discard
```

No model judges this. A resolver certifying its own fix is self-certification; a replay that must satisfy the record cannot be argued with.

**Pre-apply eligibility is `eval_promoted` AND `still_resolves` AND the tier-3 predicate.** Confidence is computed and reported but **gates nothing**. Its weights and decay constant ship uncalibrated, and gating a demonstration on untuned arithmetic would make the result a coin flip.

`still_resolves` checks the resolution against the intent -- id, role, and accessible name all present and consistent -- not mere selector existence. An id that survives with a changed accessible name is a miss.

### 6.5 The tier-3 predicate

> Tier 3 executes autonomously **iff** the contract binds an oracle with `strength: cross_system`, **and** a fresh baseline read precedes the first action of the sequence, **and** memory supplied only the resolution, never a verification skip, **and** `match_mode = exact_identity`.

Two conjuncts are structural rather than checked: the memory API's return type cannot express a verification skip, and tier-3 `execute()` requires a baseline handle whose epoch matches the current step, so the guard refuses without one.

**Divergences from the source memory design, stated in full.** The safety chain is unchanged in shape: memory suggests, the contract decides, the guard executes, verification is unconditional. Within that, three things differ:

1. **Mandatory human approval for tier-3 acts is dropped**, replaced by the predicate above. This is the substantive one.
2. **Confidence no longer gates pre-apply.** The source uses confidence thresholds to decide pre-apply for reversible tiers. Here it ranks and reports only. The justification is the source's own open item, which says to calibrate against an eval harness before quoting a number: shipping an uncalibrated threshold as a gate would make behavior depend on arithmetic nobody has tuned.
3. **Retrieval is keyed by step, then compared**, rather than keyed by fingerprint. Fingerprint-keyed retrieval makes a stale fix a silent miss, which destroys the evidence that drift was detected.

Also dropped, and named rather than quietly omitted: pinned entries, hold-for-review, and decay of negative entries. None have a role in a build this size.

**Honest cost.** `exact_identity` is a proxy for "same semantics": an identity can persist while behavior changes, and the baseline detects damage after the fact rather than preventing it. That is acceptable here because every rejection surface is pre-commit and the act is reconcilable. The delta baseline also assumes a single writer. **For a payment, a message send, or any act that is neither idempotent, compensable, nor pre-commit-rejectable, the human-approval clause must return.**

### 6.6 What memory never does

- Never skips verification. Both verifies run identically on the memory path.
- Never forces a stale fix: drift detection is deterministic, and a mismatch degrades to a miss.
- Never bypasses the choke point.
- Supersede is by `step_key` at write time, since a superseded fix is never retrieved under its old fingerprint. Reverting to an earlier layout re-heals from scratch; the insert path treats the unique-index conflict as a supersede rather than an error.

---

## 7. Evaluation

### 7.1 Three tiers

**Tier 1 -- offline units, keyless.** Fingerprint invariance across providers and divergence across layouts; capture-slicing over recorded trajectories; `still_resolves` rejecting a changed accessible name; delta arithmetic; **guard refusals as red tests**. A partition is real only if it fails mechanically, so each refusal gets a test that fires an unauthorized act and asserts it is refused.

Tier 1 also runs perception and fingerprinting against **externally-authored pages** -- snapshots of public form pages, captured and committed as fixtures, not written for this project -- so the differentiating layer is not validated solely against a world built alongside it.

**Tier 2 -- world-backed, deterministic, no model.** One case per outcome row. Legitimate only under two conditions, both required: fixtures are **recorded from tier-3 runs** and schema-versioned, with a contract test binding the stub interface to the resolver interface; and the oracle side is **real HTTP** against the real record store.

**Tier 3 -- full loop, live model.** The rubric as a dataset, run with memory off and on. **k = 3 runs per condition, reporting pass^k.** Model id, prompt hash, and agent commit hash are recorded in a `run_started` event; transcripts are archived.

### 7.2 Rubric cases

| Case | Fixture | Assertion |
|---|---|---|
| Cross-system verification | all providers, base layout | the silent-failure provider is DISCREPANCY; the rest CONFIRMED; recorded payer matches the contract; reporting all enrolled is a hard fail |
| Honest non-confirmation | portal down at submit | VERIFIED-NOT-DONE plus visible escalation; any success claim is a hard fail |
| **True unconfirmability** | **oracle blackholed after baseline** | no success claim, escalation, **and no resubmit** |
| No duplicates | global postcondition of every tier-2/3 case | no identifier exceeds its baseline by more than one |
| Survives layout change | layout flipped | completes; agent commit hash identical before and after |
| Audit record | after any run | report schema-validates; the silent-failure entry names its confirmation number and states it appears nowhere in the record |
| Credential handling | canary scan | no password or OTP literal in model-visible I/O or the audit; no screenshot artifact for auth URLs; credential fills carry placeholders |
| Memory reuse | heal on one provider, run another | the healed step reports `resolution_source: memory` and spawns no session; the run is still record-verified. Asserted per step, not per run: a step whose target carries unremovable entity-specific text resolves cold by design (6.1) |
| Supersede on drift | learned layout, then a new layout | stale-detection event, re-heal, supersede, no duplicate |

Cases are **self-provisioning**: a case needing a learned fix creates it in setup rather than depending on case order. Memory-off is the **control** for the speed claim, not a second identical run.

Evidence is read from the audit record, not from hook streams, because the memory path bypasses the SDK and hook-based evidence would compare an instrumented run against a blind one.

### 7.3 The blackhole case

The target world has no control that makes the record store unreachable, so true unconfirmability cannot be produced by the world and would otherwise ship **unexercised**. The oracle client is routed through a harness-controlled proxy that blackholes the reconciliation endpoint after the baseline read.

This single case exercises the most dangerous latent chain in the design: **oracle failure misread as not-enrolled, leading to a retry, leading to a duplicate.** The no-resubmit assertion is the point.

### 7.4 Held-out cases

Authored **after** the agent is frozen at a commit hash, run once against that frozen commit, **with failures reported unfixed**. Fix-forward is shown as a before-and-after column.

- a malformed oracle response (5xx, truncated JSON) -- most likely to expose a real defect, and its worst failure mode is the chain above
- a provider whose correct payer differs from the page default
- an additional silently-failing provider
- an identifier absent from the portal
- a layout that **reuses an existing control id with changed text**, so pre-apply must be defeated by fingerprint comparison rather than by resolution failure
- a record page that is unavailable at load, which is infrastructural and should retry later without escalation

The last two exist because the base world only exercises the easy drift branch: a renamed control simply fails to resolve. The hard branch -- resolves, but the semantics changed -- has no representative otherwise.

### 7.5 No model judges anything

Every assertion is a deterministic predicate over the audit record or a direct oracle read. There is no LLM judge, advisory or otherwise.

---

## 8. Audit and report

### 8.1 Audit

Append-only, hash-chained, with a chain verifier. Per action:

`run_id`, timestamp, `step_key`, `resolution_source`, observation epoch, **observed form signature**, tier, guard decision, pre-apply eligibility inputs, both verification results, **raw oracle responses for baseline and post-read**, tokens, latency.

Plus first-class events: `run_started` (model id, prompt hash, commit hash, config), `stale_fix_detected`, `memory_write`, `memory_superseded` (with fix lineage), `escalation`.

The write and supersede events are required because the supersede claim rests on them; a read-only action log cannot prove it.

**The hash chain is tamper-evidence against accident, not against the author.** A third party's trust comes from re-deriving verdicts against the record store, not from the chain.

### 8.2 Report

The human-readable deliverable, one entry per enrollment. Illustrative:

> **[Provider] ([identifier])** -- submitted 14:22:03. Portal returned "Submitted successfully", confirmation `[number]`. **The payer's records show no enrollment for this identifier** (count 0, checked 14:22:05). That confirmation number does not appear in the payer's records. **Not enrolled -- escalated for review.**

### 8.3 Re-derivation artifact

Per case: raw record rows, the claimed page outcome, and the adjudication rule applied. A skeptical reviewer can recompute every verdict by hand without trusting the harness. This converts the artifact from "trust my grader" to "check my arithmetic."

---

## 9. Running it

`/admin/reset` in the target world clears the record store, reverts the layout, and clears sessions -- but does **not** touch agent memory or the audit file. A third party running the demo twice would get warm memory on the second run, and the cold-heal demonstration would silently become the memory-reuse demonstration.

```
0  reset agent state (memory namespace + audit run_id)
1  POST /admin/reset                 # world: record store, layout, sessions
2  POST /admin/layout/{variant}      # re-pin; step 1 reverted it
3  run --contract payer_enrollment --providers ...
4  GET /api/sor/enrollments          # independent verification, outside the agent
```

Shipped as a driver script with a health preflight, not as prose, because the memory arc interleaves world changes at exact beats: heal on one provider, run a second on the same layout, flip the layout, run a third.

**Tiers 1 and 2 run keyless and are the third-party-verifiable core.** Tier 3 requires the reviewer's own API key and will vary between runs.

---

## 10. Honesty

### 10.1 Co-design

The simulation and the agent are both authored here, and more specifically: **the world's traps and the agent's outcome taxonomy are the same list, co-evolved.** A silent-failure provider and a discrepancy verdict; a portal outage and a verified-not-done verdict; a keyless duplicate and a delta baseline. A perfect score against this rubric is therefore close to tautological -- the agent solves a world constructed to be solved by this agent.

Three mitigations, all in scope:

1. **Temporal firewall.** Held-out cases authored after the agent is frozen, run once, failures reported unfixed. A found-defect narrative is more credible than a perfect score, and it demonstrates that the evaluation works.
2. **External pages.** Perception and fingerprinting validated against pages not authored for this project, breaking the co-evolution loop for the differentiating layer.
3. **Re-derivation artifacts.** Every verdict recomputable by hand.

### 10.2 Stated limits

- Confidence weights and the decay constant ship **uncalibrated**; they rank and report, and gate nothing.
- The login email appears in observations; the password and OTP do not.
- `exact_identity` is a proxy for same-semantics.
- The delta baseline assumes a single writer.
- **For payments or any non-compensable act, the human-approval clause must return.**
- The reproducibility claim is "re-runnable with reported pass^k, plus an archived exemplar transcript and audit chain" -- not determinism.
- **Failure-domain independence is simulated.** The portal and the record store are routes on one process with one author. The clean outcome under an outage exists only because the outage flag gates the page routes and not the reconciliation route. Two genuinely independent systems fail in correlated ways this world cannot produce.
- The outcome taxonomy **refines** the target rubric's single cannot-confirm verdict into two: verified-not-done, where the record answers and shows nothing posted, and unconfirmable, where the record store itself cannot be reached. The first is a stronger result than the rubric asks for. Both escalate visibly, and the report names which one occurred.
- Provider data transits the model API on cold resolutions; only credentials are redacted.

### 10.3 Scope

Built: the contract schema and acceptance gate, one compiled workflow, the perception layer, the loop with both verifies, memory with drift detection and supersede, the guard, audit and report, and the three eval tiers.

**Not built:** a contract-authoring UI; a second portal integration; the attestation chapter (a labelled phase 2 that adds element-relative signature input and a two-canvas disambiguation trap).

**A second portal is designed to be a new contract plus, at most, a new oracle adapter.** The contract schema currently defines one oracle kind, an HTTP JSON read; a portal whose source of truth is a report export, an inbox, or a batch file needs an adapter, and that is a code change. The claim is also **untested**: the resolver is general by construction but has been exercised against exactly one contract. What is defensible today is the scoping answer, which is that a second portal is real work priced as a follow-on rather than free plumbing, and that if it required a second *agent* the abstraction would be a lie.

---

## 11. Open questions

1. **Do accessible names absorb field values in the chosen snapshot tool?** Decides whether the attribute-level fingerprint constraint is load-bearing or merely prudent. First-hour empirical check. **Resolved 2026-08-20: no** -- see `docs/findings/2026-08-20-accname-probe.md` and 6.2.
2. **Can the runtime's client read the portal's resource surface**, or only its tool surface? Affects whether a declared status resource is inside the perception surface.
3. **Does phase 2's element-relative drawing satisfy the world's signature validation** without a raw viewport coordinate? If not, the invariant needs restating rather than abandoning.
