from vba.memory.capture import confidence, slice_capture


ENTRY = "fp-record-B"
BOUNCE = "fp-bounce"


def test_the_flagship_case_slices_to_tick_then_submit():
    """Spec 6.3. The heal trajectory on the layout that added a required checkbox:
    submit, get bounced, go back, tick, submit. The captured fix must be the last
    two actions, and MUST include the tick."""
    trace = [
        (ENTRY,  "click submit"),
        (BOUNCE, "click back"),
        (ENTRY,  "tick reviewed"),
        (ENTRY,  "click confirm-and-submit"),
    ]
    assert slice_capture(ENTRY, trace) == ["tick reviewed", "click confirm-and-submit"]


def test_the_naive_last_matching_rule_would_drop_the_tick():
    """Guards the exact defect. The observation after ticking still matches the
    entry fingerprint, because the fingerprint excludes control state."""
    trace = [
        (ENTRY,  "click submit"),
        (BOUNCE, "click back"),
        (ENTRY,  "tick reviewed"),
        (ENTRY,  "click confirm-and-submit"),
    ]
    captured = slice_capture(ENTRY, trace)
    assert "tick reviewed" in captured, "slicing dropped a required action"


def test_a_clean_run_captures_everything():
    trace = [(ENTRY, "click submit")]
    assert slice_capture(ENTRY, trace) == ["click submit"]


def test_a_trajectory_that_never_returns_to_entry_captures_nothing():
    trace = [(ENTRY, "click submit"), (BOUNCE, "click back")]
    assert slice_capture(ENTRY, trace) == []


def test_confidence_does_not_reach_one_on_a_single_success():
    """Spec 6.4: Laplace smoothing, so one lucky run does not mint trust.
    Confidence ranks and reports; it gates nothing."""
    assert confidence("cross_system", successes=1, trials=1, age_days=0) < 1.0


def test_confidence_decays_with_age():
    fresh = confidence("cross_system", successes=5, trials=5, age_days=0)
    stale = confidence("cross_system", successes=5, trials=5, age_days=90)
    assert stale < fresh


def test_cross_system_verification_scores_above_on_page():
    x = confidence("cross_system", successes=3, trials=3, age_days=0)
    y = confidence("on_page", successes=3, trials=3, age_days=0)
    assert x > y
