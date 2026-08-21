# Committed exemplar runs

Two real runs from the tier-3 evidence set, committed so the numbers in the
project README are backed by an artifact a reader can check rather than by a
claim. Everything else a run writes stays under `runs/`, which is not committed.

These two are the pair the memory-cost case compared. They are the same two
providers against the same layout, differing only in whether the agent was
allowed to use what it had learned.

| Directory | Memory | Providers |
|---|---|---|
| `memory-off-44795d7e/` | off (`--no-memory`) | 1700000001, 1700000005 |
| `memory-on-c1209ed4/` | on | 1700000001, 1700000005 |

Each holds the run's `audit.jsonl` and the `report.md` generated from it, copied
byte for byte.

## Which code produced them

The `run_started` record in each audit names commit `1802a2e`. The code under test
is commit `d24af26`: `git diff d24af26 1802a2e --stat` touches only `README.md` and
`docs/review-log.md`, so the two commits have identical source. Every tier-3 number
reported in the project README comes from that code.

The same record names `"model": "default"`, meaning the CLI behind the agent SDK
resolved the model and the run never learned which id it chose. These two runs
carry no SDK version either: the `sdk_version` field was added afterwards, in
response to this exact gap, so runs from here on record it and these two do not.
The pin they actually carry is the commit, the prompt hash, and the pinned SDK
dependency in `pyproject.toml` (`claude-agent-sdk==0.2.139`). See the model note in
the project README's stated limits.

## What a reader can recompute

**The chain.** Every record carries a `row_hash` over its own content and its
predecessor's hash. Recompute it:

```python
import json, pathlib
from vba.audit.chain import verify_chain

records = [json.loads(line) for line in
           pathlib.Path("docs/evidence/memory-on-c1209ed4/audit.jsonl")
           .read_text(encoding="utf-8").splitlines()]
print(verify_chain(records))        # (True, None)
```

Both files return `(True, None)`. Edit any field in either and the same call
returns the index of the first record that no longer agrees with the chain.

**Every verdict in the report.** The reports are generated from these audits and
from nothing else. For each `verification` record, the outcome follows from four
values in that record: the record-store reading taken before the act (`baseline`),
the reading taken after (`after`), the confirmation number the portal displayed
(`page_confirmation`), and the entity the run was about (`entity`). The interesting
one is provider 1700000005: `baseline.count` and `after.count` are both 0, so
nothing posted, while the portal returned a success page carrying a confirmation
number that appears nowhere in the payer's records. That is the `discrepancy`
outcome and the "Not enrolled. Escalated for review." line in the report.

**The memory claim.** Count `session_message` records in each file and compare the
`source` field on `action` records:

```
memory off   121 session messages   18 actions   0 replayed from memory
memory on    108 session messages   18 actions   1 replayed from memory
```

Identical verdicts in both, and the one warm action is the second provider's
submit, replayed from a fix learned on the first with no model call.

**The scrubber.** Neither file contains the staging password or the authenticator
code. Grep for `Staging2026!` or `246810` and you will find nothing, which is the
same property the rubric's credential canary asserts.

## What is not here

Session transcripts. The audit records that a session message occurred and its
type, not its content, so there is no transcript to archive. This is a stated
divergence from the design document, recorded in the project README's stated
limits.
