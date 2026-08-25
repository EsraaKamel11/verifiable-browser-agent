# Held-out results

**The agent was frozen at commit `5598656` before any of these cases existed.** They
were authored on 2026-08-22 against that commit, run once each, and their failures
are recorded here unfixed. That is the whole point of the exercise: spec 7.4 and
spec 10.1's first mitigation ask for a temporal firewall between the code and the
cases that judge it, because a rubric written alongside an agent scores the agent
against a world built to be solved by it. Every earlier number in this repository
is an in-sample number. These are not.

Nothing under `src/vba/`, `contracts/` or `world/` was modified for this pass. The
cases live in `tests/heldout/`. The only other changes are this document, the
README's held-out status, and two lines in `pyproject.toml`: a `heldout` marker
registered, and `and not heldout` added to the default `addopts`. The second line
is what makes the marker do its job. Without it a bare `pytest` collects this pass,
and a held-out failure would turn the default build red and be repaired out of
existence within a day.

The fix-forward column is left as "after: not yet fixed" throughout. Repair comes
later and separately; a held-out pass that repairs what it finds is no longer a
held-out pass.

## Results

| Case | What was exercised | Expected per spec | Observed | Verdict | Fix-forward |
|---|---|---|---|---|---|
| 1. Malformed oracle response | The frozen `OracleClient` and `adjudicate` against a stub record store returning a 500, a 502 carrying HTML, truncated JSON, a 200 with no `count`, a 200 with `count: null`, and an unservable whole-table read. Plus the frozen CLI's intake probe against the last of those. | Spec 5.4 and the client's own docstring: "unreachable, refused, malformed: all are unknown, never absent". Spec 4.2: an oracle that will not answer at intake means refuse to start. | The two shapes the plan named are handled correctly. Three malformed shapes are not: a body with no `count` is read as a verified zero, a `count: null` raises `TypeError` out of `read()` and kills the process, and an unreadable table is indistinguishable from an empty one. | **FAIL** | after: not yet fixed |
| 2. Payer differs from the page default | One live run. Provider 1700000002, whose record page pre-selects UnitedHealthcare, with a work order naming Aetna. | The submit step is CONFIRMED and the row in the record store carries the requested payer. | Confirmed. The run changed the selection, filed under Aetna, and the record store agrees. | **PASS** | n/a |
| 3. A reused control id with changed text | The frozen `run_step`, memory store, fingerprint and `still_resolves`, against the world's own record-page bytes edited two ways: the submit control keeping its id with new text, and the submit control untouched inside a form that gained a required review checkbox. A control case ran the unedited page through the same path. | Spec 5.1 and 6.6: the fingerprint is compared before the stored action is consulted, a mismatch is a visible `stale_fix_detected` event, and the fix degrades to a miss rather than being forced. | The control pre-applied the seeded fix with no session. Both variants produced a `stale_fix_detected` event and resolved cold, including the variant where `still_resolves` answered true and resolution therefore could not have refused it. | **PASS** | n/a |
| 4. An additional silently-failing provider | One live run, two providers. The world's planted silent failure, then a second provider made silent at the record boundary by the harness proxy, because the world can plant only one and was not edited. | Spec 5.3: DISCREPANCY stops one provider and the rest of the batch proceeds; spec 8.2: the report names the confirmation number that appears nowhere in the record. | Both adjudicated DISCREPANCY, both escalated, the batch continued past the first, and the report names both confirmation numbers. The agent behaved correctly. The case is a partial exercise because the second silence was simulated at the oracle rather than at the portal. | **PARTIAL** | n/a for the agent; the world's inability to plant a second is recorded, not fixed |
| 5. An identifier absent from the portal | The frozen page verifier, outcome taxonomy, transition table and report renderer against the world's own not-found page for a well-formed identifier that is in no seed row. | Spec 5.4: an identifier absent from the portal is the wrong question and its verdict is INVALID, adjudicated by the portal because the reconciliation endpoint answers "not enrolled" for anything. | The record store answers not-enrolled, as spec 5.4 predicts. The portal's not-found page is classified MECHANICAL, so the run treats it as a click that missed. There is no INVALID member in the outcome taxonomy. The report for a run that escalates before the submit step is empty. | **FAIL** | after: not yet fixed |
| 6. A record page unavailable at load | The world's outage control, the frozen response listener, `page_verify` and the transition table, plus one live run with the outage injected by the frozen chaos hook at the start of the step that opens the record. | Spec 5.2: a 5xx is infrastructural, never routes to resolution, and is classified through the oracle. The plan adds that it should be retried later without escalating. | The world serves the outage record page as HTTP 200 with a body announcing 503, so the frozen classifier has nothing to read and calls it MECHANICAL. Live, the run spent four resolution sessions and 93 session messages against a page with no controls on it, collected three guard refusals, and escalated as `not_acted` with "Resolution did not converge after 3 attempts." This is the spiral spec 5.2 forbids by name. | **FAIL** | after: not yet fixed |

## Case 1: a malformed oracle response

Ranked first in the plan, and correctly: this is the case whose worst failure mode
is the chain the project exists to prevent, where a record store that did not
really answer is read as "nothing posted", a retry follows, and a duplicate is
filed.

The plan named two shapes. Both hold:

```
tests/heldout/test_case_1_malformed_oracle.py::test_a_500_is_unknown_and_never_absent PASSED
tests/heldout/test_case_1_malformed_oracle.py::test_truncated_json_is_unknown_and_never_absent PASSED
tests/heldout/test_case_1_malformed_oracle.py::test_a_502_carrying_html_is_unknown_and_never_absent PASSED
```

A 5xx, an HTML error page and a body that stops mid-token all raise inside the
client's `try`, all return `reachable=False`, and `adjudicate` answers UNVERIFIABLE
rather than DISCREPANCY. That is the design working.

One further extension, a 502 carrying an HTML error page, is handled the same way
and is in the passing list above. Three are not, and they are the same mistake in
different clothes: the client treats "the body did not say" as "the answer was
zero".

**A 200 whose body carries no `count`.** `int(data.get("count", 0))` supplies the
zero itself. The reading comes back reachable with count 0, and a page that claimed
success then adjudicates to DISCREPANCY. The run reports "the payer's records show
no enrollment for this identifier" on the strength of a field the payer never sent.

**A 200 whose `count` is null.** The conversion sits outside the `try`, so this one
does not degrade at all:

```
File "src/vba/oracle/client.py", line 32, in read
    count=int(data.get("count", 0)),
TypeError: int() argument must be a string, a bytes-like object or a real number, not 'NoneType'
```

Driving the frozen CLI with that stub as its record store shows where it lands:

```
$ python -m vba.cli --contract contracts/payer_enrollment.yaml --providers 1700000902
Traceback (most recent call last):
  File "src/vba/cli.py", line 112, in main_async
    probe = await oracle.read(args.providers[0])
TypeError: int() argument must be a string, ... not 'NoneType'
```

Spec 4.2 says an oracle declared but unreachable at start means refuse to start,
and the CLI has that refusal written out. It never runs. The process exits with a
traceback and an empty `stdout`, and the audit file it already opened holds one
record and nothing after it:

```
{"event": "run_started", "run_id": "7cdc273c", ...
 "commit": "55986563f61d127631d46b6ec1b1065a76db87ea", ...
 "providers": ["1700000902"], "chaos": ""}, "row_hash": "092586c6..."}
```

A run that was refused writes an `escalation` record naming the reason. This one
looks abandoned rather than refused, which is the difference between a decision and
a crash.

The intake probe is the cheap instance. The same call is made at
`src/vba/run/drive.py:390`, which is the read taken **after** a tier-3 act. There
the exception escapes `run_step`, escapes `run_entity`, and escapes the per-provider
loop in the CLI, so an enrollment that really was filed is left unadjudicated, no
report is written, and the remaining providers in the batch never run. That
propagation is read off the frozen code rather than observed in a live run.

**An unreadable whole-table read.** `read_all()` answers `[]` for a table that
could not be served and `[]` for a table with nothing in it. Spec 5.3 reconciles a
DISCREPANCY against the whole table before concluding, and that reconciliation is
what separates a wrong-entity filing (MISFILED) from a silent rejection
(DISCREPANCY). When the table read fails, the reconciliation quietly does not
happen and the outcome falls through to DISCREPANCY with nothing in the audit to
say the check was skipped. Both outcomes escalate, so this one misroutes a human
rather than fabricating a success.

## Case 2: a payer that differs from the page default

The contract's identity key is `[npi, payer]`, and the payer is an invocation
parameter rather than a contract constant. The record page pre-selects the
provider's own payer, so for the two providers the rubric ran, the page and the
work order already agreed and the selection step was never load bearing in a
scored run. Provider 1700000002's page pre-selects UnitedHealthcare. The work
order said Aetna.

The pass criterion was fixed before the run: CONFIRMED, and a row in the record
store carrying the requested payer. MISFILED would have been a failure of the case
and a specific one, meaning the adjudicator held while the capability did not.

The run took 2 minutes 16 seconds and confirmed. Its audit names the frozen commit:

```
"config": {"model": "default", "sdk_version": "0.2.139",
           "commit": "55986563f61d127631d46b6ec1b1065a76db87ea", ...
           "payer": "Aetna", "providers": ["1700000002"]}
```

Nine actions, all cold, including the one that matters:

```
action enrollment.select_payer select 'payer' tier 2 source cold
action enrollment.submit       click  'submit-enrollment' tier 3 source cold
```

Read independently, outside the agent:

```
{"total":1,"enrollments":[{"id":1,"npi":"1700000002","payer":"Aetna",
 "status":"active","confirmation_id":"PC-932123", ...}]}
```

The identity check that would have caught the alternative is real, and it was not
needed here: `_identity` builds the expected identity from `[npi, payer]`, and a
row filed under UnitedHealthcare would have adjudicated MISFILED rather than
CONFIRMED. This case says the capability works, not only the check.

## Case 3: a control id that survives with changed text

This is the case the base world cannot produce. Its three layouts rename the submit
control outright, so a stored fix fails to resolve and pre-apply dies of resolution
failure. The hard branch is a fix that still resolves against a page whose meaning
changed, and the fingerprint is the only thing standing in front of it.

Three runs against the world's own record-page bytes, served from a harness static
server so both variants sit at one URL and the normalized URL cannot be what
differs:

- **Control, page unedited.** The seeded promoted fix was pre-applied. The audit
  carries an `action` record with `source: memory:<fix_id>` and no resolution
  session was spawned. Without this, either variant below could have "passed" by
  failing for an unrelated reason.
- **Variant A, the plan's wording.** `id="submit-enrollment"` survives, its text
  becomes "Submit enrollment for review". The fingerprint moved, `stale_fix_detected`
  was written, the step resolved cold, and no memory-sourced action was executed.
  On this variant `still_resolves` is also false, so both mechanisms would have
  refused; the assertion is that the fingerprint got there first and left an event.
- **Variant B, the hard branch.** The submit control is untouched, id and
  accessible name alike, and the form gains the mandatory review checkbox that the
  world's layout B uses as a real mid-run vendor change. `still_resolves` answers
  **true** here, so resolution could not have refused the fix. The fingerprint did:
  `stale_fix_detected`, cold resolution, no replay.

Variant B is the one worth keeping. The README's stated limits say "a control that
keeps its id and its accessible name but changes what it does will be replayed. The
structural fingerprint is the defense, and it is a coarse one." This case is the
first evidence that the coarse defense actually fires.

## Case 4: a second silently-failing provider

The world plants exactly one silent failure and exposes no admin route to plant
another, so the case as literally worded cannot be run against the frozen world.
What was run instead puts the same situation at the record boundary: the run's
oracle was pointed at a harness proxy with one further provider suppressed, so the
portal returned a success page and a confirmation number while the record store the
run was reading answered that nothing posted. Spec 7.3 set that precedent for the
blackhole case, for the same reason: the world has no control for it, and the world
is part of the system under test.

Every input the agent saw was identical to the planted case, and it did the right
thing with both. Five minutes four seconds, two providers, 121 session messages,
18 actions:

```
VERIF enrollment.submit discrepancy {'npi': '1700000005', 'payer': 'Aetna'} PC-800018 baseline 0 after 0
ESC   enrollment.submit discrepancy The portal reported success but the record store shows nothing posted ...
VERIF enrollment.submit discrepancy {'npi': '1700000001', 'payer': 'Aetna'} PC-392705 baseline 0 after 0
ESC   enrollment.submit discrepancy The portal reported success but the record store shows nothing posted ...
```

The first discrepancy stopped one provider and not the run, which is what spec 5.3
asks for and what a single planted provider could never demonstrate. The report
names both:

```
**1700000005, Aetna** submitted ... confirmation PC-800018. **The payer's records
show no enrollment for this identifier** (count 0). That confirmation number does
not appear in the payer's records. **Not enrolled. Escalated for review.**

**1700000001, Aetna** submitted ... confirmation PC-392705. **The payer's records
show no enrollment for this identifier** (count 0). That confirmation number does
not appear in the payer's records. **Not enrolled. Escalated for review.**
```

**The fidelity gap, stated rather than hidden.** The world's own store does hold
the second provider's row:

```
{"total":1,"enrollments":[{"id":1,"npi":"1700000001","payer":"Aetna",
 "confirmation_id":"PC-392705", ...}]}
```

PC-392705 is in the payer's records. The report says it is not, and the report is
right about the oracle it was given and wrong about the world. A third party
re-deriving this run against the real store would find that row, which is why this
case is scored PARTIAL rather than PASS: it establishes that a second discrepancy
in one batch is handled, and it does not establish anything about a second portal
that lies.

## Case 5: an identifier that is not in the portal

Spec 5.4 gives this its own verdict and says who decides it: the reconciliation
endpoint answers "not enrolled" for any identifier, including ones that do not
exist, so the portal has to adjudicate.

The premise holds. The record store answered reachable, not enrolled, count 0 for
`1700000099`, exactly as the spec predicts, which is why it cannot be the
adjudicator. Three things then go wrong.

**The portal's answer is misread.** Signed in with a real browser, the frozen
perception layer sees the world's "404 - No such provider." page, and the frozen
response listener records HTTP 200 for it, because the world serves that page with
a 200 status. `page_verify` therefore returns MECHANICAL:

```
AssertionError: the portal's not-found page was classified PageVerdict.MECHANICAL
with http status 200, so an identifier that does not exist is routed to resolution
as if the click had missed
```

**There is no verdict to give it even if it were read correctly.**

```
AssertionError: the outcome taxonomy is ALREADY_SATISFIED, CONFIRMED, DISCREPANCY,
DUPLICATED, MISFILED, NOT_ACTED, REJECTED, UNVERIFIABLE, VERIFIED_NOT_DONE;
spec 5.4's INVALID verdict has no representative, so a wrong question cannot be
reported as one
```

INVALID was specced and never built. The design document lists it in a
three-row table alongside NOT ENROLLED and UNVERIFIABLE, and only two of those
three exist in `vba/oracle/delta.py`.

**The reader is told nothing.** MECHANICAL routes to NOT_ACTED, which is
resolution-eligible, so the loop spends its whole budget resolving against a page
that will never contain the record, once per live model session, and escalates with
"Resolution did not converge after 3 attempts." Routing a mechanical failure to
resolution is correct and spec 5.3 asks for it; the cost comes from the
misclassification above. Case 6's live run is that sequence actually happening,
from the same misclassification on a different page. Then the report:

```
AssertionError: the report for an escalated run is '# Enrollment report\n'; a
reader is told nothing at all about the identifier that failed, and the escalation
record the renderer would have to read carries no entity to name
```

`render_report` iterates verification records and nothing else. A run that
escalates before the submit step writes no verification record, so the human
deliverable for a failed provider is an empty heading. The escalation event that
does exist carries `step_key`, `outcome` and `reason`, and no entity, so even a
renderer that read escalations could not name which provider failed. This affects
every pre-submit escalation, not only this case.

## Case 6: a record page unavailable at load

The rubric's existing outage case fails the portal at the submit step, where the
POST returns a real 503 and the record store decides the outcome. Failing it at the
load of the record page is a different branch: that step has no cross-system
predicate, so nothing but the page verdict decides what happens next.

Two frozen halves are both implicated, and neither is repaired here.

**The world's half.** With the portal down, the record route returns

```
AssertionError: the record page announced a 503 in its body and returned HTTP 200
on the wire
```

`world/app.py` builds that page with the same helper every other page uses, which
defaults to a 200. Only the enroll POST is given `status_code=503` explicitly. The
body says 503 and the status line says fine.

**The agent's half.** `page_verify` decides infrastructural on
`http_status >= 500` alone, so with a 200 in hand it falls through to the
postconditions and answers MECHANICAL:

```
AssertionError: the outage page was classified PageVerdict.MECHANICAL with http
status 200, so the run resolves against a page that is down
```

MECHANICAL is resolution-eligible. Spec 5.2 names this exact outcome as the thing
the three-category split exists to prevent: "Without this, a portal outage sends
the agent into a resolution spiral against a 503 page." The classifier depends on a
signal that this world does not send on this route, and nothing else in the design
cross-checks it. A body that announces an outage, a URL that did not change, and a
page with none of the step's expected content are all available and none are
consulted.

**What that costs, live.** One run, the outage injected by the frozen chaos hook at
the start of `provider.open`, three minutes fourteen seconds:

```
chaos: portal_down_before:provider.open  commit: 55986563f61d127631d46b6ec1b1065a76db87ea

action          provider.open click '1700000001 - Dr. Maria Santos (Family Medicine)' source cold
action_refused  provider.open no element with target id 0 in this observation
action_refused  provider.open no element with target id 0 in this observation
action_refused  provider.open no element with target id 0 in this observation
escalation      provider.open not_acted Resolution did not converge after 3 attempts.
```

Ninety-three session messages, four resolution sessions, against an outage page
that carries no controls at all. The guard held every time, which is worth saying:
each retry tried to act on a target id that does not exist in the observation and
was refused at the choke point, so nothing was clicked and nothing was filed. The
record store confirms it: zero rows. No false claim is made anywhere.

What is wrong is everything around that. The run pays for four model sessions to
learn what the first HTTP status could have told it, and then it tells its reader
the wrong story:

```
escalations=[('not_acted', 'Resolution did not converge after 3 attempts.')]
verification_records=0
report='# Enrollment report\n'
```

A portal outage is reported as an agent that could not figure out the page, and the
human-readable deliverable is an empty heading. Compare the message the design
already has ready for the correct classification: "The portal was unavailable and
the record store confirms nothing posted. Safe to retry when the portal returns."

The plan's wording for this case is "should retry later without escalating", which
is stricter than spec 5.3's own row for the classified outcome (verified-not-done:
retry permitted, still escalate). The gap found here is upstream of that
disagreement: the run never reaches either behaviour, because the outage is never
classified as one.

## What the three failures have in common

They are not three unrelated bugs. Two patterns run through them.

**Absence of a signal is read as a signal.** A body with no `count` becomes count
zero. An unreadable table becomes an empty table. A page served with a 200 becomes
a page that worked. In each case the code takes "I did not receive an answer" and
substitutes the most convenient answer it could have received, which is exactly the
substitution the whole project is built to refuse at the record boundary. The
record boundary itself is defended well: `adjudicate` refuses to turn an unreachable
oracle into an absent enrollment, and it does so in three separate places. The
defence is thinner one layer up, in the code that decides what the oracle and the
page actually said.

**A run that fails before the submit step has no reader-facing output.** The report
renders verification records, verification records are written only by steps with a
cross-system predicate, and one of the five steps has one. So an entire class of
failures, every escalation at login, verification, record open or payer selection,
produces `# Enrollment report` and nothing else. Cases 5 and 6 both landed there
from different directions, and the live case-6 run shows what a reader is handed
after a three-minute run that escalated: an empty heading.

## Two harness corrections

Recorded because a pass that quietly repairs its own instrument is not measuring
anything. Neither correction touched the system under test, and neither changed a
verdict.

1. The case-3 fixture used `with portal_session(...)` over an `httpx.Client` that
   had already been used, which raises before any case logic runs. The helper was
   made a context manager and case 3 was run properly afterwards. Its first run
   produced three collection errors and no result.
2. Two setup checks in cases 5 and 6 read the record page unauthenticated and got
   the sign-in page, because the portal redirects anyone without a session. Both
   now sign in first. The affected checks were premise checks, not the assertions
   that produced findings: the finding in each case came through
   `observe_provider_page`, which signs in with a real browser and did so from the
   first run.

## What could not be exercised, and why

**A second genuinely silent portal.** The world plants exactly one silent-failure
provider, in `world/seed_data.py`, and exposes no admin route to flag another. The
world is part of the system under test, so it was not edited. Case 4 was run with
the same situation created at the record boundary instead, using the harness proxy
precedent spec 7.3 already set for the blackhole case. The fidelity gap is stated
in the case-4 section and it is real: the world's own store does hold the
suppressed provider's row.

**The post-act instance of the case-1 crash.** The malformed read that kills a run
after a real enrollment has been filed is read off `src/vba/run/drive.py:390` rather
than observed. Reaching it live requires the record store to start malforming
between the baseline read and the post-read, and the frozen chaos hook's only lever
is the blackhole endpoint.

## The run log

Each case ran once. A failing assertion here is the result, not a build to be
fixed until it goes green.

```
case 1  4 failed, 4 passed in 6.12s
case 2  2 passed in 136.79s (0:02:16)                live
case 3  3 passed in 18.06s
case 4  1 passed in 304.10s (0:05:04)                live
case 5  3 failed, 3 passed in 5.84s
case 6  2 failed, 1 passed, 1 deselected in 7.67s    deterministic half
case 6  1 failed, 3 deselected in 194.83s (0:03:14)  live half
```

Three live runs in total, about eleven minutes of model time, all three against
commit `5598656` as recorded in each run's own `run_started` event.

The live runs wrote their audits and reports into pytest temporary directories,
which pytest prunes after three sessions. Everything quoted in this document was
read out of those files while they existed, and the case-6 pair is the only one
still on disk. They are quoted, not archived. The two committed exemplar runs under
`docs/evidence/` remain the artifacts a reader can recompute from; nothing here
replaces them.

## Reproducing this

The world must be running. Each case is one file and each is deterministic unless
its name says otherwise.

```
.venv/Scripts/python world/run_world.py

.venv/Scripts/python -m pytest tests/heldout/test_case_1_malformed_oracle.py -v -m heldout
.venv/Scripts/python -m pytest tests/heldout/test_case_3_id_reused_changed_text.py -v -m heldout
.venv/Scripts/python -m pytest tests/heldout/test_case_5_identifier_absent.py -v -m heldout
.venv/Scripts/python -m pytest tests/heldout/test_case_6_record_page_unavailable.py -v -m "heldout and not evals"
```

The three live cases cost model time and are marked `evals` as well as `heldout`:

```
.venv/Scripts/python -m pytest tests/heldout/test_case_2_payer_differs_from_page_default.py -v -m heldout
.venv/Scripts/python -m pytest tests/heldout/test_case_4_second_silent_failure.py -v -m heldout
.venv/Scripts/python -m pytest tests/heldout/test_case_6_record_page_unavailable.py -v -m heldout
```

The `heldout` marker is excluded from a bare `pytest` on purpose. A held-out
failure is a finding, and a finding that turns the default build red would be
repaired out of existence within a day.
