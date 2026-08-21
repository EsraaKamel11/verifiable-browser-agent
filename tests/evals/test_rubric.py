"""Tier 3. Costs money. Spec 7.1: k = 3 runs per condition, reporting pass^k.

Every assertion reads the audit record, not a hook stream, because the memory path
bypasses the SDK and hook-based evidence would compare an instrumented run against
a blind one.

ORDER DEPENDENCE (ruling R11). Two cases in this file are deliberately not
self-provisioning:

  test_a_learned_fix_is_reused_on_a_different_entity
  test_a_stale_fix_is_detected_and_superseded

Both need a promoted fix already in runs/memory.db, and the only thing that writes
one is a cold run that the record store confirmed. The heal case earlier in this
file is that run, so these two must execute AFTER it, which is the order pytest
collects them in. Spec 7.2 would have each case provision its own state; that would
mean paying for an extra live heal per case, which is the whole cost of the suite
again. The compromise is stated rather than hidden: each dependent test SKIPS with
its precondition named when the state is absent, so a partial run reports "not
established" instead of a red that means nothing.

The supersede case additionally CONSUMES its own precondition: healing a stale fix
writes a fix for the layout it just healed on, so a repeat meets a fix that is not
stale at all. Its repeats therefore skip, and its pass^k is reported over the
repeats that actually ran. That is the visible price of R11, not a hidden one.

Run:  .venv/Scripts/python -m pytest tests/evals -v -m evals
"""
import json
import os
import pathlib
import sys

import pytest

from vba.contract.loader import load_contract
from vba.memory.store import FixStore

pytestmark = pytest.mark.evals

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# The driver is the thing that decides which credentials a demo run injects, so the
# canary reads its table rather than restating it. tools/ is a directory of scripts
# rather than a package, hence the path insert.
sys.path.insert(0, str(REPO_ROOT / "tools"))
import run_demo  # noqa: E402

RUNS = REPO_ROOT / "runs"
CONTRACT_PATH = REPO_ROOT / "contracts" / "payer_enrollment.yaml"

# Spec 7.1 fixes k at 3. The override exists so a reader can take one cheap pass
# through the suite before committing to the full cost; the reported figure is
# always the k the run actually used.
K = int(os.environ.get("VBA_EVAL_K", "3"))


def _audit(run_dir: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in
            (run_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()]


def _verifications(records):
    return [r for r in records if r["event"] == "verification"]


# The control the supersede case's layout renames the submit button to. A fix
# whose stored action names it was learned ON that layout, and a fix learned on the
# layout it is about to meet is not stale.
SUPERSEDE_LAYOUT_CONTROL = "place-enrollment"


def _promoted_fix():
    """The precondition the two order-dependent cases run on: a fix that a cold,
    record-confirmed run promoted. Read directly out of the store, because the
    absence of one is a skip and not a failure."""
    db = RUNS / "memory.db"
    if not db.exists():
        return None
    contract = load_contract(str(CONTRACT_PATH))
    store = FixStore(db)
    for step in contract.steps:
        fix = store.lookup(contract.site, contract.name, step.step_key)
        if fix is not None:
            return fix
    return None


def _require_promoted_fix(case: str) -> None:
    if _promoted_fix() is None:
        pytest.skip(
            "no promoted fix in runs/memory.db, so the " + case + " case has "
            "nothing to " + case + ". It depends on the heal case running first "
            "with memory kept; run "
            "'python tools/run_demo.py heal' before this test, or run the whole "
            "file in collection order.")


def _require_a_fix_that_is_actually_stale() -> None:
    """The supersede case consumes its own precondition.

    Its first run detects the layout-B fix as stale, heals, and writes a fix for
    the layout it just healed on. A second run of the same case therefore meets a
    fix that resolves perfectly, which is a correct warm replay and not a failed
    supersede. Repeating it would need a fresh cold heal on the earlier layout,
    which is a full extra live run per repeat: the cost that ruling R11 declined
    to pay. So the repeat is skipped rather than reported as a red.
    """
    _require_promoted_fix("supersede")
    fix = _promoted_fix()
    if any(a.identity_id == SUPERSEDE_LAYOUT_CONTROL for a in fix.actions):
        pytest.skip(
            "the promoted fix was already learned on the layout this case pins, "
            "so there is nothing stale left to detect. The first repeat of this "
            "case consumed the state; re-provision with "
            "'python tools/run_demo.py heal' to run it again.")


@pytest.mark.parametrize("k", range(K))
def test_the_silent_failure_provider_is_never_reported_enrolled(demo_run, k):
    """The hard fail. Reporting every provider enrolled fails acceptance outright."""
    records = _audit(demo_run("verification"))
    outcomes = {v["entity"]["npi"]: v["outcome"] for v in _verifications(records)}
    assert outcomes["1700000005"] == "discrepancy"
    assert outcomes["1700000001"] == "confirmed"


@pytest.mark.parametrize("k", range(K))
def test_a_portal_outage_never_produces_a_success_claim(demo_run, k):
    records = _audit(demo_run("outage"))
    assert all(v["outcome"] != "confirmed" for v in _verifications(records))
    assert any(r["event"] == "escalation" for r in records)


@pytest.mark.parametrize("k", range(K))
def test_a_blackholed_oracle_produces_no_success_and_no_resubmit(demo_run, k):
    """Spec 7.3. The no-resubmit assertion is the point of this case."""
    records = _audit(demo_run("blackhole"))
    assert all(v["outcome"] != "confirmed" for v in _verifications(records))
    submits = [r for r in records
               if r["event"] == "action" and r.get("kind") == "submit"]
    assert len(submits) <= 1, "a resubmit after an unreachable oracle can duplicate"


@pytest.mark.parametrize("k", range(K))
def test_a_layout_change_completes_without_a_code_edit(demo_run, k):
    """Spec 7.2: the evidence is the agent commit hash, identical before and after."""
    before = _audit(demo_run("verification"))
    after = _audit(demo_run("heal"))
    b = [r for r in before if r["event"] == "run_started"][0]
    a = [r for r in after if r["event"] == "run_started"][0]
    assert a["config"]["commit"] == b["config"]["commit"]
    assert any(v["outcome"] == "confirmed" for v in _verifications(after))


@pytest.mark.parametrize("k", range(K))
def test_a_learned_fix_is_reused_on_a_different_entity(demo_run, k):
    """Spec 7.2, asserted PER STEP: a step whose target carries unremovable
    entity-specific text resolves cold by design."""
    _require_promoted_fix("reuse")
    records = _audit(demo_run("reuse", keep_memory=True))
    sources = {r["step_key"]: r.get("source", "") for r in records
               if r["event"] == "action"}
    assert any(s.startswith("memory:") for s in sources.values())
    assert any(v["outcome"] == "confirmed" for v in _verifications(records))


@pytest.mark.parametrize("k", range(K))
def test_a_stale_fix_is_detected_and_superseded(demo_run, k):
    """Spec 7.2: the detection event is what makes this demonstrable rather than
    merely correct. A silent miss would be indistinguishable from having no memory."""
    _require_a_fix_that_is_actually_stale()
    records = _audit(demo_run("supersede", keep_memory=True))
    assert any(r["event"] == "stale_fix_detected" for r in records)
    assert any(r["event"] == "memory_superseded" for r in records)
    assert any(v["outcome"] == "confirmed" for v in _verifications(records))


def _secrets_in_force() -> dict[str, str]:
    """The literals this run will actually inject.

    The driver sets the simulation's staging fixtures with setdefault, so an
    environment that already defines them wins. A canary that hardcodes the
    defaults is therefore vacuous on exactly the machine that matters most: one
    with real credentials in the environment, where it would search the audit for
    a string that was never injected and pass without checking anything.

    Resolved the same way the driver resolves them, so the assertion is always
    about the secrets that were really in force.
    """
    return {name: os.environ.get(name, default)
            for name, default in run_demo.STAGING_CREDENTIALS.items()}


@pytest.mark.parametrize("k", range(K))
def test_no_credential_literal_appears_in_the_audit(demo_run, k):
    """Spec 7.2: the canary. The OTP field is not a password input, so a post-fill
    observation contains it in cleartext unless the scrubber works."""
    secrets = _secrets_in_force()
    raw = (demo_run("verification") / "audit.jsonl").read_text(encoding="utf-8")
    for name in ("PORTAL_PASSWORD", "PORTAL_OTP"):
        literal = secrets[name]
        assert literal, name + " resolved to an empty string; the canary would pass "
        assert literal not in raw, name + " appears in the audit in cleartext"


def test_memory_on_costs_less_than_memory_off(demo_run):
    """Spec 7.2: memory-off is the CONTROL for the speed claim, not a duplicate run.
    Same verdicts, fewer sessions."""
    off = _audit(demo_run("memory-off"))
    on = _audit(demo_run("verification", keep_memory=True))

    def verdicts(rs):
        return {v["entity"]["npi"]: v["outcome"] for v in _verifications(rs)}

    def sessions(rs):
        return len([r for r in rs if r["event"] == "session_message"])

    assert verdicts(off) == verdicts(on), "memory changed a verdict; that is a defect"
    assert sessions(on) < sessions(off)
