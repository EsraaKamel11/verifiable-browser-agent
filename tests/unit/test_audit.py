from vba.act.actions import Action, ActionContext
from vba.audit.chain import chain_hash, verify_chain
from vba.audit.log import AuditLog
from vba.contract.gate import Grant
from vba.contract.schema import Step
from vba.perceive.elements import Observation, elements_from_records


def test_a_chain_verifies_when_untouched(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl", run_id="r1")
    log.run_started({"model": "m", "commit": "abc123"})
    log.verification("enrollment.submit", "confirmed", {"count": 1}, {"count": 0})
    ok, bad = verify_chain(log.records())
    assert ok is True and bad is None


def test_editing_a_record_breaks_the_chain_at_that_point(tmp_path):
    """Spec 8.1: tamper evidence against accident, not against the author."""
    log = AuditLog(tmp_path / "audit.jsonl", run_id="r1")
    log.run_started({"model": "m", "commit": "abc123"})
    log.verification("enrollment.submit", "confirmed", {"count": 1}, {"count": 0})
    log.escalation("enrollment.submit", "discrepancy", "the page lied")
    records = log.records()
    records[1]["detail"] = "tampered"
    ok, bad = verify_chain(records)
    assert ok is False and bad == 1


def test_the_audit_records_the_resolution_source_for_every_action(tmp_path):
    """Spec 7.2: the memory-reuse assertion reads this field, so it must exist on
    every action record, not only on memory hits."""
    log = AuditLog(tmp_path / "audit.jsonl", run_id="r1")
    log.action("enrollment.submit", kind="submit", target="submit-enrollment",
               source="memory:fix-1", epoch=3, tier=3, permitted=True,
               form_signature="fs-A")
    rec = [r for r in log.records() if r["event"] == "action"][0]
    assert rec["source"] == "memory:fix-1"
    assert rec["form_signature"] == "fs-A"


_LOGIN_ELEMENTS = elements_from_records([
    {"tag": "button", "role": "button", "name": "Sign in", "element_id": "sign-in",
     "name_attr": "", "input_type": "submit", "is_submit": True,
     "selector": "#sign-in"},
    {"tag": "a", "role": "link", "name": "Help", "element_id": "help",
     "name_attr": "", "input_type": "", "is_submit": False, "selector": "#help"},
])
_LOGIN_OBS = Observation(url="http://portal/", epoch=1, elements=_LOGIN_ELEMENTS,
                         text="", fingerprint="fp-login")
_FULL = Grant(max_tier=3, reason="cross-system oracle bound")


def _permit(log, target_id, step):
    ctx = ActionContext(step=step, grant=_FULL, observation=_LOGIN_OBS,
                        baseline=None)
    element = _LOGIN_OBS.by_id(target_id)
    log.action_permitted(Action(kind="click", target_id=target_id, value=None,
                                step_key=step.step_key, epoch=1),
                         element, ctx)
    return [r for r in log.records() if r["event"] == "action"][-1]


def test_an_exempted_act_says_in_the_record_that_it_fired_a_form(tmp_path):
    """Spec 4.3, 8.1. Once a step can be exempted from the shaping rule, the tier
    on an action record no longer tells a reader what happened: a tier-2 record can
    now be a fired form. The one place the exemption is used must not be the one
    place the audit is silent, so the record carries the element's own submit
    metadata and the step's own declaration."""
    log = AuditLog(tmp_path / "audit.jsonl", run_id="r1")
    login = Step(step_key="portal.login", intent="sign in", tier=2, fires_form=True)

    rec = _permit(log, 0, login)

    assert rec["tier"] == 2
    assert rec["is_submit"] is True
    assert rec["fires_form"] is True


def test_an_ordinary_tier_2_click_is_distinguishable_from_an_exempted_one(tmp_path):
    """The control. If both records looked the same the fields would prove nothing."""
    log = AuditLog(tmp_path / "audit.jsonl", run_id="r1")
    ordinary = Step(step_key="enrollment.select_payer", intent="pick", tier=2)

    rec = _permit(log, 1, ordinary)

    assert rec["tier"] == 2
    assert rec["is_submit"] is False
    assert rec["fires_form"] is False


def test_memory_write_and_supersede_are_first_class_events(tmp_path):
    """Spec 8.1: the supersede claim rests on these; a read-only action log cannot
    prove a fix was ever replaced."""
    log = AuditLog(tmp_path / "audit.jsonl", run_id="r1")
    log.memory_write("fix-2", "enrollment.submit", "fp-C")
    log.memory_superseded("fix-1", "fix-2", "fingerprint changed")
    events = {r["event"] for r in log.records()}
    assert {"memory_write", "memory_superseded"} <= events


def test_a_refusal_is_recorded_not_swallowed(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl", run_id="r1")
    log.action_refused("provider.open", kind="click", target="submit-enrollment",
                       reason="element is a submit control but step is tier 1")
    rec = [r for r in log.records() if r["event"] == "action_refused"][0]
    assert "submit control" in rec["reason"]
