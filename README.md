# A browser agent that cannot report work it did not do

Most browser agents decide for themselves whether they succeeded. They read the
page, see "Submitted successfully", and report success. This one is not allowed to.
Whether work posted is decided outside the agent, by reading a source of truth the
model has no access to and cannot decline to consult, and the answer is compared
against a reading taken before the agent acted.

The workload is provider enrollment: sign in to a payer portal, open a provider
record, select the payer named in the work order, file the enrollment, and confirm
it posted. The portal is a simulation that ships with this repository, and it lies
in the specific way real portals lie.

## The five demonstrations

1. **Verification.** Two providers are filed. The portal returns "Submitted
   successfully" and a confirmation number for both. One of them posted nothing at
   all, and the confirmation number it showed is a fabrication. The agent
   reports one enrolled and one discrepancy, escalated, and the report says which
   confirmation number does not appear in the payer's records.
2. **Self-healing.** The portal's vendor changes the layout: the submit control is
   renamed, moved, and gated behind a new review checkbox. The agent resolves the
   new page and completes the enrollment with no code change. The evidence is the
   agent's own commit hash, recorded in the audit before and after and identical.
3. **Memory reuse.** The fix learned during the heal is replayed on a different
   provider without a model call for that step. The audit records the fix id
   against every action it produced, so a warm run is distinguishable from a cold
   one after the fact.
4. **Supersede on drift.** A third layout arrives. The stored fix is detected as
   stale before it is applied, a fresh resolution replaces it, and both the
   detection and the replacement are first-class audit events. A memory that
   silently misses looks exactly like having no memory at all.
5. **Honest failure.** The portal goes down at the moment of filing, and separately
   the record store stops answering after the act. Neither produces a success
   claim. The first is reported as provably not filed and safe to retry; the second
   as unknown, escalated, and never retried, because a retry against an unknown
   files a duplicate.

Two properties hold across all five: no credential literal ever reaches the audit
file or the model, and no page, however emphatic, can decide that work posted.

## Running the keyless tiers

Nothing here needs an API key or costs money.

```
python -m venv .venv
.venv/Scripts/pip install -e ".[dev,world]"
.venv/Scripts/playwright install chromium
```

**Tier 1, unit.** Pure logic against fakes: the guard, the adjudicator, the memory
store and its templating, capture slicing, the audit chain, the report, the run
loop's transition table, and perception against three pages captured from the open
web that nobody here authored.

```
.venv/Scripts/python -m pytest
```

A bare `pytest` runs this tier only. The other two are excluded by marker, on
purpose: one needs a server and the other spends money, and neither should be
reachable by typing the obvious command.

**Tier 2, world-backed.** Real Playwright against the real simulation, real HTTP
against the real record store, no model. It spawns and tears down the world itself.

```
.venv/Scripts/python -m pytest tests/world -m world
```

## Running the demonstrations

Start the simulated portal and leave it running:

```
.venv/Scripts/python world/run_world.py
```

Then, in another shell, one demonstration at a time:

```
.venv/Scripts/python tools/run_demo.py verification
.venv/Scripts/python tools/run_demo.py heal
.venv/Scripts/python tools/run_demo.py reuse --keep-memory
.venv/Scripts/python tools/run_demo.py supersede --keep-memory
.venv/Scripts/python tools/run_demo.py memory-off
.venv/Scripts/python tools/run_demo.py outage
.venv/Scripts/python tools/run_demo.py blackhole
```

These spend money: each unresolved step opens a model session, capped per session.

The order of the middle three matters and the driver cannot enforce it for you. The
portal's reset endpoint reverts the layout, clears sessions and empties the record
store, but it does not touch the agent's memory or its audit files. So `heal` runs
cold and learns a fix; `reuse --keep-memory` replays that fix on a different
provider; `supersede --keep-memory` meets a third layout and must notice the fix no
longer applies. Drop `--keep-memory` and the driver wipes the agent's state first,
which is what you want for every other case and fatal for those two: the reuse
demonstration would silently become a second cold run and still pass its own eyes.

**Credentials.** The driver sets the simulated portal's own staging login, staging
password and fixed staging authenticator code into the environment before invoking
the agent, so a third party can run all seven cases with no setup. They are the
vendored simulation's seed fixtures, not anyone's secrets. A real deployment sets
`PORTAL_EMAIL`, `PORTAL_PASSWORD` and `PORTAL_OTP` itself and the driver leaves
them alone.

**The eval suite** runs the same seven cases as a dataset, three times per
condition, reporting pass^k:

```
.venv/Scripts/python -m pytest tests/evals -v -m evals
```

## What a run produces

Each run writes `runs/<run_id>/`:

- `audit.jsonl`, one hash-chained record per event: the run's model, prompt hash
  and agent commit; every permitted action with its tier, its target and whether it
  came from memory or a live resolution; every refused action with the guard's
  stated reason; every stale-fix detection, memory write and supersede; every
  verification with the record-store readings taken before and after; and every
  escalation.
- `report.md`, the same run written for someone who has to act on it.

The report is one line per verification, naming the entity, the time, what the
portal showed, what the payer's records show, and the verdict. The interesting one is the
provider whose portal said "Submitted successfully": its line reports that the
payer's records hold no enrollment for that identifier and that the confirmation
number the portal minted does not appear in them, and it ends "Not enrolled.
Escalated for review."

Every verdict in it can be recomputed by hand from the two record-store readings,
the page verdict and the confirmation number, all of which are in the audit file.

The learned fixes live in `runs/memory.db`, one row per fix, with the page
fingerprint it was learned on, the exact action sequence with entity values
replaced by parameter references, and its provenance.

## Evaluation tooling

Two of the seven cases need the world to change during a run, at a point no
operator could hit by hand: the portal must fail after the agent has committed to
filing, and the record store must stop answering after it has been read once and
before it is read again. That timing is the whole content of both cases.

So the harness carries a chaos hook: an environment variable, read at two fixed
points in the run loop, that posts to an admin endpoint. It is inert unless the
variable names both a directive and the step it applies to, the demo driver is the
only thing that sets it, and the driver restores the world afterwards even when the
run fails. It is evaluation tooling living in production code, which is worth
saying out loud rather than leaving for a reader to find.

The record store is reached through a small proxy for the second case, because the
simulation exposes no control that makes it unreachable. The driver starts and
stops that proxy itself.

## Honesty

### The tautology

Both the simulation and the agent are authored here. More precisely: the world's
traps and the agent's outcome taxonomy are the same list, evolved together. A
silently failing provider and a discrepancy verdict. A portal outage and a
verified-not-done verdict. A keyless duplicate submission and a delta baseline. A
perfect score against this rubric is therefore close to tautological. The agent
solves a world built to be solved by this agent.

Three things push back on that, and none of them dissolves it:

1. **A temporal firewall.** A held-out set of cases, authored only after the agent
   is frozen at a commit, run once, with its failures reported unfixed. A narrative
   with a found defect in it is more credible than a perfect score, and it is the
   only evidence that the evaluation itself works. **This one has not run yet.** It
   is the next piece of work, and until it does, every rubric result reported here
   is an in-sample result and should be read as one.
2. **External pages.** Perception and structural fingerprinting are validated
   against three pages captured from the open web that were not written for this
   project, which breaks the co-evolution loop for the layer that most needs it.
3. **Re-derivation.** Every verdict in every report can be recomputed by hand from
   the audit file, so a reader does not have to take the summary's word for it.

The review log in `docs/review-log.md` is part of this section rather than separate
from it. It records what each review round caught, including three defects found
while assembling the agent for its first live run: that it could not sign in to any
portal at all, that a resolution session was never told which provider or payer it
was working on, and that four of its five steps had no evidence that could make
them fail. Every test in the repository was green throughout.

### Stated limits

- Confidence weights and the decay constant ship **uncalibrated**. They rank and
  report; they gate nothing.
- A reused fix's trial and success counts are **not updated on reuse**. Confidence
  is frozen at the value it was captured with. Since it gates nothing, this is
  cosmetic today, and it would not be if it ever gated anything.
- The login email appears in observations. The password and the authenticator code
  do not, and a test asserts that neither literal appears anywhere in the audit.
- **Exact identity matching is a proxy for same semantics.** A control that keeps
  its id and its accessible name but changes what it does will be replayed. The
  structural fingerprint is the defense, and it is a coarse one.
- **The form signature omits the form's action and method.** Two pages with the
  same URL shape, the same named controls and the same buttons, posting to
  different endpoints, fingerprint identically. Nothing in this world produces
  that collision; a second portal might.
- **The form-firing exemption is per step, not per form.** A step the contract
  declares as form-firing may fire any form reachable from its page at its own
  tier, not only its own. In this world the authentication wall makes that
  unreachable, and the exemption is off by default and still capped by the intake
  grant, but it is a genuine narrowing of the guarantee that only a tier-3 step
  with a live baseline can submit anything.
- **The observation carries no element state.** Whether a checkbox is already
  ticked or which option a dropdown has selected is not shown to the model, so a
  retry cannot tell what a previous attempt already did and may undo it. This was
  observed live before the intermediate steps were given postconditions.
- **A fix cannot be captured from a trajectory that starts on a refusal page.**
  Capture slices the action suffix that begins at the step's entry state, so an
  attempt that starts on a bounce page, navigates back and then succeeds captures
  nothing. That is conservative rather than wrong, since a fix keyed to a bounce
  page would be the wrong thing to replay, but it means a heal that takes two
  attempts teaches the agent nothing.
- **Only steps with a cross-system predicate are learned.** Promotion requires the
  record store to have confirmed the step, so the four steps that are judged on the
  page alone never write a fix and always resolve cold.
- **The delta baseline assumes a single writer.** Another process filing records
  concurrently would make the arithmetic wrong.
- **For payments, or any act that cannot be compensated, the human-approval step
  has to come back.** It is deliberately absent here.
- The reproducibility claim is "re-runnable, with a reported pass^k, an archived
  exemplar transcript and a hash-chained audit". It is not determinism.
- **Failure-domain independence is simulated.** The portal and the record store are
  two route groups in one process with one author. The clean result under an
  outage exists because the outage flag gates the page routes and not the
  reconciliation route. Two genuinely independent systems fail in correlated ways
  this simulation cannot produce.
- The outcome taxonomy **refines** the usual single cannot-confirm verdict into
  two: verified-not-done, where the record store answers and shows nothing posted,
  and unconfirmable, where the record store cannot be reached at all. The first is
  a stronger result than the requirement asks for. Both escalate visibly, and the
  report names which one happened.
- Provider identifiers transit the model API during cold resolutions. Only
  credentials are redacted.

### What is not built

Built: the contract schema and its acceptance gate, one compiled workflow, the
perception layer, the run loop with both verifications, memory with drift detection
and supersede, the guard, the audit and the report, and all three evaluation tiers.

Not built: a contract-authoring interface; a second portal integration; and the
attestation chapter, a labelled second phase that adds element-relative signature
input and a two-canvas disambiguation trap. The simulation already carries the
switch for that phase, off by default.

**A second portal is designed to be a new contract plus, at most, a new oracle
adapter.** The contract schema defines one kind of source of truth, an HTTP JSON
read. A portal whose truth lives in a report export, an inbox or a nightly batch
file needs an adapter, and that is a code change. The claim is also untested: the
resolver is general by construction but has been exercised against exactly one
contract. What is defensible today is the scoping answer. A second portal is real
work, priced as a follow-on rather than as free plumbing, and if it required a
second agent then the abstraction would be a lie.
