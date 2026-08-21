import pytest

from vba.act.actions import Action, ActionContext
from vba.act.choke import execute
from vba.contract.gate import Grant
from vba.contract.schema import Step
from vba.guard.tiers import GuardRefusal, check
from vba.guard.credentials import CredentialVault
from vba.guard.scrub import Scrubber
from vba.perceive.elements import Observation, elements_from_records


class FakeBaseline:
    def __init__(self, epoch: int = 7):
        self.epoch = epoch


ELEMENTS = elements_from_records([
    {"tag": "a", "role": "link", "name": "open record", "element_id": "",
     "name_attr": "", "input_type": "", "is_submit": False, "selector": "a"},
    {"tag": "button", "role": "button", "name": "Submit enrollment",
     "element_id": "submit-enrollment", "name_attr": "", "input_type": "submit",
     "is_submit": True, "selector": "#submit-enrollment"},
])
OBS = Observation(url="http://h/p/1", epoch=7, elements=ELEMENTS, text="", fingerprint="f")

TIER1 = Step(step_key="provider.open", intent="open", tier=1)
TIER3 = Step(step_key="enrollment.submit", intent="file it", tier=3,
             satisfied_when="oracle.confirmed")
FULL = Grant(max_tier=3, reason="ok")


def _ctx(step, baseline=None, grant=FULL, obs=OBS):
    return ActionContext(step=step, grant=grant, observation=obs, baseline=baseline)


def test_a_click_on_a_submit_control_during_a_tier_1_step_is_refused():
    """Spec 4.3 shaping rule. The resolver called it a click; the element metadata
    says it is a submit. Metadata wins, or a lower-tier step can fire the form."""
    a = Action(kind="click", target_id=1, value=None, step_key="provider.open", epoch=7)
    with pytest.raises(GuardRefusal, match="submit"):
        check(a, _ctx(TIER1))


def test_a_tier_3_act_without_a_baseline_is_refused():
    """Spec 6.5: tier-3 execute requires a baseline handle, so the guard refuses
    without one. This is one of the two structural conjuncts."""
    a = Action(kind="submit", target_id=1, value=None,
               step_key="enrollment.submit", epoch=7)
    with pytest.raises(GuardRefusal, match="baseline"):
        check(a, _ctx(TIER3, baseline=None))


def test_a_tier_3_act_with_a_stale_baseline_is_refused():
    """The baseline must belong to THIS step, not an earlier one."""
    a = Action(kind="submit", target_id=1, value=None,
               step_key="enrollment.submit", epoch=7)
    with pytest.raises(GuardRefusal, match="baseline"):
        check(a, _ctx(TIER3, baseline=FakeBaseline(epoch=3)))


def test_a_tier_3_act_beyond_the_grant_is_refused():
    """Spec 4.2: an on-page or absent oracle caps autonomy at intake."""
    a = Action(kind="submit", target_id=1, value=None,
               step_key="enrollment.submit", epoch=7)
    limited = Grant(max_tier=2, reason="on-page only", propose_only_tiers={3})
    with pytest.raises(GuardRefusal, match="grant"):
        check(a, _ctx(TIER3, baseline=FakeBaseline(), grant=limited))


def test_a_stale_epoch_is_refused_rather_than_translated():
    """Spec 4.3: a target id from a stale observation is refused, never silently
    re-bound, because the element it named may now be a different element."""
    a = Action(kind="click", target_id=0, value=None,
               step_key="provider.open", epoch=3)
    with pytest.raises(GuardRefusal, match="epoch"):
        check(a, _ctx(TIER1))


def test_an_unknown_target_id_is_refused():
    a = Action(kind="click", target_id=99, value=None,
               step_key="provider.open", epoch=7)
    with pytest.raises(GuardRefusal, match="target"):
        check(a, _ctx(TIER1))


def test_the_authorized_tier_3_submit_is_permitted():
    """The control: the guard must not refuse everything."""
    a = Action(kind="submit", target_id=1, value=None,
               step_key="enrollment.submit", epoch=7)
    check(a, _ctx(TIER3, baseline=FakeBaseline(epoch=7)))  # does not raise


def test_a_tier_1_read_is_permitted():
    a = Action(kind="click", target_id=0, value=None,
               step_key="provider.open", epoch=7)
    check(a, _ctx(TIER1))  # does not raise


# --- The contract-declared form exemption (spec 4.3) ---

LOGIN = Step(step_key="portal.login", intent="sign in", tier=2, fires_form=True)
SELECT = Step(step_key="enrollment.select_payer", intent="pick the payer", tier=2)


def test_a_declared_form_firing_step_may_fire_its_own_form_without_a_baseline():
    """Every portal signs in through a submit control, so without an explicit
    exemption the shaping rule refuses the login button and no contract that
    requires authentication can run at all. The exemption is declared in the
    contract, per step, and it keeps the step at its own tier so the tier-3
    baseline requirement does not apply to an act that posts no record."""
    a = Action(kind="click", target_id=1, value=None,
               step_key="portal.login", epoch=7)
    check(a, _ctx(LOGIN, baseline=None))  # does not raise


def test_an_undeclared_tier_2_step_still_cannot_fire_a_form():
    """The control, and the case the rule exists for: the payer-selection step
    runs on the page that carries the real enrollment submit button, and it must
    not be able to post an unbaselined record."""
    a = Action(kind="click", target_id=1, value=None,
               step_key="enrollment.select_payer", epoch=7)
    with pytest.raises(GuardRefusal, match="submit"):
        check(a, _ctx(SELECT))


def test_the_exemption_cannot_buy_a_tier_3_step_out_of_its_baseline():
    """The safety regression. The exemption is written to apply only below tier 3,
    so a tier-3 step that also declared it still needs a live baseline. Nothing but
    the shape of one expression enforces that today, and an act that files a record
    without a baseline is the single failure this whole guard exists to prevent, so
    it is pinned here rather than left to survive the next refactor by luck."""
    submits_and_declares = Step(step_key="enrollment.submit", intent="file it",
                                tier=3, fires_form=True,
                                satisfied_when="oracle.confirmed")
    a = Action(kind="submit", target_id=1, value=None,
               step_key="enrollment.submit", epoch=7)
    with pytest.raises(GuardRefusal, match="baseline"):
        check(a, _ctx(submits_and_declares, baseline=None))
    # And the same step WITH a fresh baseline is still permitted, so the pin above
    # is about the baseline and not about the flag breaking tier 3 outright.
    check(a, _ctx(submits_and_declares, baseline=FakeBaseline(epoch=7)))


def test_a_declared_form_firing_step_is_still_capped_by_the_grant():
    """Spec 4.2 outranks the exemption: a tier-1 grant refuses a tier-2 act
    whether or not the contract declared the step form-firing."""
    a = Action(kind="click", target_id=1, value=None,
               step_key="portal.login", epoch=7)
    tier1_only = Grant(max_tier=1, reason="no oracle binding")
    with pytest.raises(GuardRefusal, match="grant"):
        check(a, _ctx(LOGIN, grant=tier1_only))


# --- Choke-point smoke tests ---


class FakePage:
    """Records every method call with its arguments."""
    def __init__(self):
        self.calls = []

    async def click(self, selector):
        self.calls.append(("click", selector))

    async def fill(self, selector, value):
        self.calls.append(("fill", selector, value))

    async def select_option(self, selector, value):
        self.calls.append(("select_option", selector, value))

    async def hover(self, selector):
        self.calls.append(("hover", selector))

    async def goto(self, url):
        self.calls.append(("goto", url))

    async def evaluate(self, script, *args):
        self.calls.append(("evaluate", script, args))


class FakeAudit:
    """Records action_permitted calls."""
    def __init__(self):
        self.calls = []

    def action_permitted(self, action, element, ctx):
        self.calls.append(("action_permitted", action, element, ctx))


async def test_a_refused_action_produces_no_side_effect():
    """Spec 3.1: the guard is a partition; a refusal prevents any side effect."""
    # Tier-1 click on a submit control is refused
    a = Action(kind="click", target_id=1, value=None, step_key="provider.open", epoch=7)
    page = FakePage()
    audit = FakeAudit()
    vault = CredentialVault({})
    scrubber = Scrubber()

    with pytest.raises(GuardRefusal):
        await execute(a, _ctx(TIER1), page, audit, vault, scrubber)

    # No side effects recorded
    assert len(page.calls) == 0, "guard refusal should prevent any page calls"
    assert len(audit.calls) == 0, "guard refusal should prevent audit recording"


async def test_a_permitted_action_reaches_the_browser_after_guard_passes():
    """The guard must not refuse valid actions; permitted actions reach the browser."""
    # Tier-1 click on a regular link is permitted
    a = Action(kind="click", target_id=0, value=None, step_key="provider.open", epoch=7)
    page = FakePage()
    audit = FakeAudit()
    vault = CredentialVault({})
    scrubber = Scrubber()

    await execute(a, _ctx(TIER1), page, audit, vault, scrubber)

    # Exactly one browser call with the correct selector
    assert len(page.calls) == 1
    assert page.calls[0] == ("click", "a"), \
        f"expected click('a'), got {page.calls[0]}"

    # Exactly one audit call
    assert len(audit.calls) == 1
    name, action, element, ctx = audit.calls[0]
    assert name == "action_permitted"
    assert action is a
    assert element.selector == "a"
    assert ctx.step.tier == 1
