"""The MCP tool handler's refusal path. Ruling R22(b).

CLAUDE.md tells every resolution session that a refused attempt "is recorded" and
that it should "read the reason and satisfy it". Both promises live or die in this
handler: before this, a GuardRefusal escaped it as an unexplained tool error and no
record was written at all.
"""
from vba.act.actions import ActionContext
from vba.act.server import action_tools
from vba.contract.gate import Grant
from vba.contract.schema import Step
from vba.guard.credentials import CredentialVault
from vba.guard.scrub import Scrubber
from vba.perceive.elements import Observation, elements_from_records


ELEMENTS = elements_from_records([
    {"tag": "a", "role": "link", "name": "open record", "element_id": "open",
     "name_attr": "", "input_type": "", "is_submit": False, "selector": "a"},
    {"tag": "button", "role": "button", "name": "Submit enrollment",
     "element_id": "submit-enrollment", "name_attr": "", "input_type": "submit",
     "is_submit": True, "selector": "#submit-enrollment"},
])
OBS = Observation(url="http://h/p/1", epoch=7, elements=ELEMENTS,
                  text="Enrollment form", fingerprint="fp")
TIER1 = Step(step_key="provider.open", intent="open the record", tier=1)
FULL = Grant(max_tier=3, reason="cross-system oracle bound")


class FakePage:
    def __init__(self):
        self.calls = []

    async def click(self, selector):
        self.calls.append(("click", selector))


class FakeAudit:
    def __init__(self):
        self.permitted = []
        self.refused = []

    def action_permitted(self, action, element, ctx):
        self.permitted.append(action)

    def action_refused(self, step_key, **fields):
        self.refused.append((step_key, fields))


class FakeHolder:
    """The same shape Task 14's CtxHolder presents to the handler."""

    def __init__(self, ctx):
        self.current = ctx
        self.trace = []
        self.refreshes = 0

    def record(self, action):
        self.trace.append(action)

    async def refresh(self):
        self.refreshes += 1
        return OBS


def _tools(holder, page, audit):
    return {t.name: t for t in action_tools(holder, page, audit,
                                            CredentialVault({}), Scrubber())}


def _ctx(step):
    return ActionContext(step=step, grant=FULL, observation=OBS, baseline=None)


async def test_a_refused_tool_call_records_the_attempt_and_states_the_reason():
    """Spec 4.3 and 5.2. The session is told WHY, so it can satisfy the reason
    rather than repeating the action until the turn budget runs out."""
    page, audit = FakePage(), FakeAudit()
    holder = FakeHolder(_ctx(TIER1))
    click = _tools(holder, page, audit)["click"]

    result = await click.handler({"target_id": 1, "value": ""})

    assert audit.permitted == [], "a refusal must not record a permitted action"
    assert len(audit.refused) == 1, "exactly one refusal record"
    step_key, fields = audit.refused[0]
    assert step_key == "provider.open"
    assert fields["kind"] == "click"
    assert fields["target"] == "submit-enrollment"
    assert "submit" in fields["reason"]

    text = result["content"][0]["text"]
    assert "Refused" in text and "submit" in text
    assert result.get("is_error") is True
    assert page.calls == [], "the guard is a partition; nothing reached the browser"
    assert holder.trace == [], "a refused action is not part of the captured fix"
    assert holder.refreshes == 0, "no action landed, so there is nothing to re-perceive"


async def test_a_permitted_tool_call_still_acts_records_and_re_perceives():
    """The control. The refusal branch must not have swallowed the normal path."""
    page, audit = FakePage(), FakeAudit()
    holder = FakeHolder(_ctx(TIER1))
    click = _tools(holder, page, audit)["click"]

    result = await click.handler({"target_id": 0, "value": ""})

    assert audit.refused == []
    assert len(audit.permitted) == 1
    assert page.calls == [("click", "a")]
    assert len(holder.trace) == 1
    assert holder.refreshes == 1
    assert "Elements:" in result["content"][0]["text"]


async def test_an_unknown_target_id_is_refused_without_raising():
    """The one refusal reason whose own audit record cannot look the target up."""
    page, audit = FakePage(), FakeAudit()
    holder = FakeHolder(_ctx(TIER1))
    click = _tools(holder, page, audit)["click"]

    result = await click.handler({"target_id": 99, "value": ""})

    assert len(audit.refused) == 1
    assert audit.refused[0][1]["target"] == "target_id=99"
    assert "Refused" in result["content"][0]["text"]
