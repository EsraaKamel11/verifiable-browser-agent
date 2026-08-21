from vba.audit.chain import chain_hash, verify_chain
from vba.audit.log import AuditLog


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
