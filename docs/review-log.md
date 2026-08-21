# Review log

Every task in this build was written, then reviewed against the design document by
an independent reviewer, then repaired. This file records what the reviews caught.

It is part of the deliverable rather than process trivia. Read the findings
together and a pattern shows up: almost none of them would have produced an
obvious failure. They would have produced a plausible wrong story. A memory
subsystem that never promoted anything and therefore never reused anything. A
confirmation path that reported success on evidence it had not actually checked.
An agent that could not sign in, in a repository whose tests were all green. That
is the same failure mode this agent is built to catch in the work it does, so
catching it in the work that built the agent is the honest test of the method.

Each entry names the finding, the repair, and the commit that carries it.

## Pre-flight scan, before any code

A cross-task consistency pass over the plan, looking for pairs of tasks that share
a file or an interface. Six conflicts, before a line was written:

- A tier-3 action sequence would have been refused on its second action, because
  the guard requires the baseline to belong to the current observation and nothing
  carried it forward. The flagship self-heal is a two-action sequence, so the
  headline demonstration was structurally impossible. Repaired by re-stamping the
  baseline's epoch, never its reading, on every fresh observation.
- Nothing anywhere promoted a learned fix, and the memory store only reads
  promoted ones. Memory would have been written, never read. The reuse,
  supersede, and memory-cost demonstrations would all have run cold and no test
  would have said so.
- Nothing re-perceived the page between a resolution session's actions, so the
  model would have acted twice against a snapshot taken before its first action,
  and capture would have recorded both actions under one stale fingerprint.
- Superseding a stored fix happened silently inside a SQL statement, so the
  supersede demonstration had no event to assert on.
- The run's bindings carried only the identifier, while the contract's identity
  key is identifier plus payer. A record filed under the wrong payer would have
  read as confirmed.
- Two rubric cases called demonstrations that the demo driver did not define.

## Independent design review, still before any code

A second reviewer read the plan against the design document and found two things
the consistency pass had not:

- Nothing anywhere recorded whether an action came from memory or from a live
  resolution, so every audit record would have said "cold" and the memory-reuse
  demonstration would have had no evidence to point at. Provenance now travels
  with the action context.
- The field page verification uses to tell a server error apart from a missing
  control was never written by any code in the plan. A portal returning 503 would
  have been classified as a mechanical failure and routed into a resolution
  session against a dead page, which the design forbids by name. A response
  listener now tracks the main document's status.

## Task 1, the skeleton

An em-dash in a findings document, against the project's own prose rule. Removed
and the prose swept (commit fc1239a).

A second finding, that the probe hardcodes the simulated portal's seeded login,
was contested and parked: the value is the simulation's own fixture and the world
accepts no other, so a literal reading of the no-names rule is untenable.

## Task 5, the choke point

The guard was tested, but the function that actually performs the side effect was
not, so nothing pinned the property the whole design rests on: that a refusal
means no side effect happened. Two smoke tests added, implementation untouched
(commit 9bf3b90).

## Task 6, credentials and scrubbing

Two findings, one critical. The reported test count could not be reconciled with
the committed tree, because an updated test file was never committed. It was
(commit 9d69d33).

The second: a credential reference was detected by looking for a colon anywhere in
the value, so an ordinary value containing a colon would have been treated as a
secret reference and refused. Replaced with a full-match rule. The implementer's
pattern was stricter than the one proposed in review and was kept for a reason the
review had missed: the proposed pattern would have matched a time of day (commit
ac6afc7).

## Task 7, the adjudicator

The critical one. A submission could be reported CONFIRMED when the page showed no
confirmation number at all, because only two of the three required agreements were
checked. Confirmation now requires all three, and a missing page confirmation is a
discrepancy that escalates rather than a success (commit 948160e).

Also: an unreachable baseline reading defaults its count to zero, and zero was
being treated as a known-empty starting point. That is the unknown-read-as-absent
chain, and it can mint a confirmation out of nothing. Unreachable is now checked
before any arithmetic.

## Task 9, the memory store

SQLite connections were committed but never closed. On Windows, with a database
file the demo deletes and recreates between runs, a leaked handle is not a
theoretical tidiness issue. Fixed with a context manager that closes in a finally
(commit 4931763).

## Task 11, the action server and the resolution session

The tool-grant mechanism auto-approved the granted tools but did not remove the
ungranted ones from the model's context, so a step that was never granted the
submit tool could still call it and be refused only at the guard. The runtime's own
documentation names the field that actually removes a tool. Both are now passed,
computed as a set difference so the granted and ungranted lists cannot drift apart
(commit 2a0cc05).

## Task 13, the audit and the report

The report prefixed every outcome with "Portal returned a success page", including
the outcome that exists precisely because the portal returned an error. The prefix
is now conditioned on the outcome, with a test pinning it (commit 069951a).

## Task 14, the run loop

Two findings, both about a miss being read as a success.

A stored fix whose target had vanished returned a mechanical failure immediately.
A vanished element and a portal that has stopped answering are indistinguishable at
that point, and collapsing them sends an outage into a resolution session, which
the design forbids by name. The loop now abandons the replay and lets page
verification classify what it finds (commit cb3d934).

And: four of the five contract steps declared no postconditions, so page
verification with nothing to check answers PASSED. An abandoned replay on such a
step would have been reported as a success and the run would have walked on
instead of healing. Only a PASSED verdict is downgraded, so a stated refusal or an
outage still survives as the more specific answer.

The same round added an item the design required and no task implemented: a
recorded failed approach is injected into every later resolution of that step, so
one that outlives the failure it describes is a permanent blinder. Failed
approaches are now retired when the step succeeds.

## Task 15, perception against pages nobody here wrote

The critical one, and the most instructive. One of the three externally captured
fixtures was not the page it claimed to be: the capture tool had followed a link
to a well-known standards site and saved a bot-challenge interstitial, two footer
links and no forms. The test suite was green, the fixture count was right, and the
report described a rich standards page that was not in the repository.

Repaired by replacing the fixture with a verified form-rich page, and by adding a
loud guard to the capture tool so a challenge page cannot be saved as evidence
again. The guard was checked against the original URL to confirm it fires (commit
267cc79).

## Task 16, the world tier

An invariant test asserting that no identifier is ever enrolled more than once was
vacuous: it ran against an empty table, so it passed by having nothing to check,
and its description said otherwise. It now files one real enrollment first and then
asserts the invariant over a non-empty table (commit 68d0ac8).

## Task 17, the first end-to-end runs

The last task is where the agent was first assembled and run end to end against the
live world with a live model. Three defects came out of it, none of which any
keyless or world-tier test could have reached, because all three live in the seam
between the model, the contract and the guard. Two were caught by reading before
the first paid run; the third needed three live runs to find.

**The agent could not sign in.** Found by probe rather than by run: a snapshot of
the live login page shows the sign-in button carrying submit metadata, and the
guard classifies any submit-type control as the highest tier and refuses it below
that tier with no baseline. That is correct for the control that files a record and
wrong for the one that signs you in, and every portal signs in through a submit
control. Left alone, the login step would have been refused, the step would still
have reported success because it declared no postconditions, and every later step
would have acted on the login page.

The design document anticipates the shape of the answer: an approved act needs an
explicit exemption rather than an implicit one. Steps now declare form-firing in
the contract. It is off by default, it applies per step, and the intake grant still
caps it, so the step that runs on the page carrying the real enrollment button is
still refused. Recorded in the README's stated limits, because it narrows a
flagship guarantee.

**The resolution session was never told which entity it was working on.** Also
found by reading. The contract's step intents carry parameter placeholders, and the
payer arrives as a run parameter rather than as contract text, but nothing rendered
either into the prompt. A session would have been handed the literal placeholder
and a request to "select the payer named in the contract" with nothing naming the
payer. It would have guessed, and a wrong guess files a real record under the wrong
identity. The bindings now reach the prompt, the intent is templated, and the
credential references the step is authorized to fill are listed by name so the
model does not have to guess those either.

**Four of the five steps could not fail.** This one only appeared in a live run.
Only the submit step declared postconditions, so page verification answered PASSED
for the other four whatever the page showed. In the first end-to-end run a session
filled the two-factor form, ticked its checkbox and stopped without submitting it,
which is exactly what its intent literally asked for. The step reported success,
the browser stayed unauthenticated, and the remaining three steps ran against a
login page and reported nothing wrong until the submit step ran out of attempts.
Every step now carries on-page evidence, and the step whose wording invited the
stop was reworded to name the confirmation it needs.

A second run, with the postconditions in place but the wording unchanged, showed
the retry problem underneath it: the model re-entered the code and clicked the
checkbox a second time, un-ticking what the first attempt had ticked, because the
enumerated elements the model is shown carry no state. That limitation is in the
README rather than fixed.

The third finding is the one worth sitting with. Nothing was broken. Every
component did what it was asked. The contract asked for something slightly
different from what it meant, and there was no evidence anywhere in the system
capable of noticing the difference. That is the failure this whole project is
about, found in the project itself, on the last day, by running it for real.
