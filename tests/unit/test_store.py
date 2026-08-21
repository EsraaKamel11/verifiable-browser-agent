# tests/unit/test_store.py
import pytest

from vba.memory.store import FixStore, LearnedFix, StoredAction
from vba.perceive.elements import Observation, elements_from_records


def _obs(records, fp="fp-A"):
    return Observation(url="http://h/p/1", epoch=1,
                       elements=elements_from_records(records), text="", fingerprint=fp)


SUBMIT_A = [{"tag": "button", "role": "button", "name": "Submit enrollment",
             "element_id": "submit-enrollment", "name_attr": "", "input_type": "submit",
             "is_submit": True, "selector": "#submit-enrollment"}]
SUBMIT_C = [{"tag": "button", "role": "button", "name": "Place enrollment",
             "element_id": "place-enrollment", "name_attr": "", "input_type": "submit",
             "is_submit": True, "selector": "#place-enrollment"}]

FIX_A = [StoredAction(kind="submit", identity_id="submit-enrollment",
                      identity_role="button", identity_name="Submit enrollment",
                      value=None, is_submit=True)]


def _fix(actions=None, fp="fp-A", **kw):
    base = dict(site="s", contract="c", step_key="enrollment.submit",
                intent="file it", page_fingerprint=fp,
                actions=actions or FIX_A, match_mode="exact_identity",
                action_tier=3, polarity="positive", provenance="eval_promoted")
    base.update(kw)
    return LearnedFix.new(**base)


def test_a_fix_resolves_when_its_identity_is_present():
    assert _fix().still_resolves(_obs(SUBMIT_A), {}) is True


def test_a_fix_does_not_resolve_when_the_control_was_renamed():
    """Spec 6.4: an id that survives with a changed accessible name is a miss."""
    assert _fix().still_resolves(_obs(SUBMIT_C), {}) is False


def test_a_changed_accessible_name_alone_is_a_miss():
    renamed = [dict(SUBMIT_A[0], name="Submit enrollment now")]
    assert _fix().still_resolves(_obs(renamed), {}) is False


def test_one_current_positive_fix_per_step_but_many_negatives(tmp_path):
    """Spec 6.1: the unique index is scoped to positive polarity."""
    store = FixStore(tmp_path / "m.db")
    store.write_candidate(_fix())
    store.write_candidate(_fix(polarity="negative", failure_mode="review required"))
    store.write_candidate(_fix(polarity="negative", failure_mode="wrong control"))
    assert len(store.negatives_for("s", "c", "enrollment.submit")) == 2


def test_writing_a_second_positive_fix_supersedes_the_first(tmp_path):
    """Spec 6.6: the insert path treats the conflict as a supersede, not an error."""
    store = FixStore(tmp_path / "m.db")
    first = _fix(fp="fp-B")
    store.write_candidate(first)
    store.promote(first.fix_id)
    second = _fix(fp="fp-C")
    store.write_candidate(second)
    current = store.lookup("s", "c", "enrollment.submit")
    assert current.fix_id == second.fix_id
    assert store.get(first.fix_id).valid_to is not None


def test_lookup_is_by_step_key_not_by_fingerprint(tmp_path):
    """Spec 5.1: keying on the fingerprint makes a stale fix a SILENT miss,
    indistinguishable from having no memory at all. The caller compares."""
    store = FixStore(tmp_path / "m.db")
    fix = _fix(fp="fp-B")
    store.write_candidate(fix)
    store.promote(fix.fix_id)
    found = store.lookup("s", "c", "enrollment.submit")
    assert found is not None
    assert found.page_fingerprint == "fp-B"


def test_a_candidate_is_not_returned_as_pre_appliable(tmp_path):
    """Spec 6.4: promotion is eval-gated; a candidate is never pre-applied."""
    store = FixStore(tmp_path / "m.db")
    store.write_candidate(_fix(provenance="candidate"))
    assert store.lookup("s", "c", "enrollment.submit") is None
