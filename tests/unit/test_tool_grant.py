from vba.act.server import allowed_tools_for, disallowed_tools_for
from vba.contract.gate import Grant
from vba.contract.schema import Step


TIER1 = Step(step_key="provider.open", intent="open", tier=1)
TIER3 = Step(step_key="enrollment.submit", intent="file it", tier=3,
             satisfied_when="oracle.confirmed")
FULL = Grant(max_tier=3, reason="ok")
CAPPED = Grant(max_tier=2, reason="on-page only", propose_only_tiers={3})


def test_a_tier_1_step_is_not_granted_the_submit_tool():
    """Spec 4.3 enforcement point 1: forced tool selection does not exist in this
    runtime, so NON-EXPOSURE is the only lever. allowed_tools_for is one half of
    that: it decides which tools are offered for auto-approval. The genuine
    removal from the model's context is disallowed_tools_for (see below);
    guard.check() at the choke point is the hard backstop regardless."""
    tools = allowed_tools_for(TIER1, FULL)
    assert not any(t.endswith("__submit") for t in tools)
    assert any(t.endswith("__click") for t in tools)


def test_a_tier_3_step_under_a_full_grant_is_granted_submit():
    assert any(t.endswith("__submit") for t in allowed_tools_for(TIER3, FULL))


def test_a_tier_3_step_under_a_capped_grant_is_not_granted_submit():
    assert not any(t.endswith("__submit") for t in allowed_tools_for(TIER3, CAPPED))


def test_the_oracle_is_never_a_tool_at_any_tier():
    """Spec 4.3: if the oracle were a tool, the model could decline to call it,
    which is the exact failure this project exists to prevent. The oracle was
    never registered as a tool at all, so it is absent from every tier's
    allowed set for that reason alone, not because it was granted and then
    filtered out."""
    for step in (TIER1, TIER3):
        for grant in (FULL, CAPPED):
            assert not any("oracle" in t or "verify" in t
                           for t in allowed_tools_for(step, grant))


def test_disallowed_tools_for_removes_exactly_the_ungranted_tools():
    """Controller ruling R19: the installed SDK's own docs say allowed_tools only
    auto-approves a call, while disallowed_tools is what actually removes a tool
    from the model's context ("removed from the model's context and cannot be
    used, even if they would otherwise be allowed" per types.py). Spec 4.3
    enforcement point 1 demands genuine non-exposure, so a tier-1 step must list
    submit in disallowed_tools_for, and never list anything it was granted.
    disallowed_tools_for is a set-difference against allowed_tools_for, so a
    tier-3 step under a full grant lists nothing it was granted either."""
    tier1_disallowed = disallowed_tools_for(TIER1, FULL)
    assert "mcp__actions__submit" in tier1_disallowed
    assert not any(t in tier1_disallowed for t in allowed_tools_for(TIER1, FULL))

    tier3_disallowed = disallowed_tools_for(TIER3, FULL)
    assert "mcp__actions__submit" not in tier3_disallowed
