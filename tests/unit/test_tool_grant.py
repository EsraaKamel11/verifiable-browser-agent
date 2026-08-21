from vba.act.server import allowed_tools_for
from vba.contract.gate import Grant
from vba.contract.schema import Step


TIER1 = Step(step_key="provider.open", intent="open", tier=1)
TIER3 = Step(step_key="enrollment.submit", intent="file it", tier=3,
             satisfied_when="oracle.confirmed")
FULL = Grant(max_tier=3, reason="ok")
CAPPED = Grant(max_tier=2, reason="on-page only", propose_only_tiers={3})


def test_a_tier_1_step_is_not_granted_the_submit_tool():
    """Spec 4.3 enforcement point 1: forced tool selection does not exist in this
    runtime, so NON-EXPOSURE is the only lever. The tool is simply absent."""
    tools = allowed_tools_for(TIER1, FULL)
    assert not any(t.endswith("__submit") for t in tools)
    assert any(t.endswith("__click") for t in tools)


def test_a_tier_3_step_under_a_full_grant_is_granted_submit():
    assert any(t.endswith("__submit") for t in allowed_tools_for(TIER3, FULL))


def test_a_tier_3_step_under_a_capped_grant_is_not_granted_submit():
    assert not any(t.endswith("__submit") for t in allowed_tools_for(TIER3, CAPPED))


def test_the_oracle_is_never_a_tool_at_any_tier():
    """Spec 4.3: if the oracle were a tool, the model could decline to call it,
    which is the exact failure this project exists to prevent."""
    for step in (TIER1, TIER3):
        for grant in (FULL, CAPPED):
            assert not any("oracle" in t or "verify" in t
                           for t in allowed_tools_for(step, grant))
