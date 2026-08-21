from dataclasses import replace

from vba.act.actions import Action, ActionContext
from vba.act.choke import execute
from vba.audit.log import AuditLog
from vba.contract.gate import Grant
from vba.contract.schema import Contract, Postcondition, Step
from vba.guard.credentials import CredentialVault
from vba.guard.scrub import Scrubber
from vba.memory.store import FixStore, LearnedFix, StoredAction
from vba.oracle.delta import Baseline, OracleReading, Outcome, PageVerdict
from vba.perceive.elements import Observation, elements_from_records
from vba.run import drive as drive_mod
from vba.run.deps import Deps
from vba.run.drive import (
    CtxHolder,
    _extract_confirmation,
    _identity,
    _MemoryDriver,
    drive,
    replay,
    run_step,
)
from vba.run.machine import run_entity


FIX_ACTIONS = [
    StoredAction(kind="click", identity_id="reviewed", identity_role="checkbox",
                 identity_name="I have reviewed this enrollment", value=None,
                 is_submit=False),
    StoredAction(kind="submit", identity_id="confirm-and-submit",
                 identity_role="button", identity_name="Confirm and submit enrollment",
                 value=None, is_submit=True),
]


class FakeFix:
    actions = FIX_ACTIONS


def test_replay_yields_every_action_in_order():
    """Spec 6.1: a fix is a SEQUENCE. Replaying only the last action drops the tick
    and the submit is bounced."""
    got = list(replay(FakeFix(), {}))
    assert [a.identity_id for a in got] == ["reviewed", "confirm-and-submit"]


def test_replay_binds_parameters_into_stored_identities():
    fix = FakeFix()
    fix.actions = [StoredAction(kind="click", identity_id="",
                                identity_role="link",
                                identity_name="{npi} - Dr. Maria Santos (Family Medicine)",
                                value=None, is_submit=False)]
    got = list(replay(fix, {"npi": "1700000001"}))
    assert got[0].identity_name == "1700000001 - Dr. Maria Santos (Family Medicine)"


def test_the_holder_records_a_trace_of_fingerprint_and_action_pairs():
    """Spec 6.3: capture slices this trace, so drive must record one entry per
    action with the fingerprint OBSERVED BEFORE that action."""
    h = CtxHolder()
    h.set_observation_fingerprint("fp-A")
    h.record("action-1")
    h.set_observation_fingerprint("fp-bounce")
    h.record("action-2")
    assert h.trace == [("fp-A", "action-1"), ("fp-bounce", "action-2")]


# --- Shared fakes. No browser, no model, no network. ---

def _elements(checkbox_name="I have reviewed this enrollment",
              submit_name="Confirm and submit enrollment"):
    return elements_from_records([
        {"tag": "input", "role": "checkbox", "name": checkbox_name,
         "element_id": "reviewed", "name_attr": "reviewed", "input_type": "checkbox",
         "is_submit": False, "selector": "#reviewed"},
        {"tag": "button", "role": "button", "name": submit_name,
         "element_id": "confirm-and-submit", "name_attr": "", "input_type": "submit",
         "is_submit": True, "selector": "#confirm-and-submit"},
    ])


ELEMENTS = _elements()

SUCCESS_TEXT = "Submitted successfully. Confirmation number: PC-000123"
REFUSAL_TEXT = "Please confirm you have reviewed this enrollment before submitting."

STEP3 = Step(
    step_key="enrollment.submit", intent="file this enrollment with the payer", tier=3,
    satisfied_when="oracle.confirmed",
    postconditions=[Postcondition(text_present="Submitted successfully"),
                    Postcondition(text_absent="Please confirm you have reviewed")],
)
FULL = Grant(max_tier=3, reason="cross-system oracle bound")

ENTRY_OBS = Observation(url="http://portal/enroll", epoch=0, elements=ELEMENTS,
                        text="Enrollment form", fingerprint="fp-entry")
DONE_OBS = replace(ENTRY_OBS, text=SUCCESS_TEXT)


class FakePage:
    def __init__(self, url="http://portal/enroll"):
        self.url = url
        self.calls = []
        self.settles = 0
        self.handlers = []
        self.main_frame = object()

    async def click(self, selector):
        self.calls.append(("click", selector))

    async def fill(self, selector, value):
        self.calls.append(("fill", selector, value))

    async def wait_for_load_state(self, state):
        self.settles += 1

    def on(self, event, handler):
        self.handlers.append((event, handler))


def _install_snapshot(monkeypatch, plan):
    """Replace the module-level snapshot binding drive() actually calls.

    Each planned Observation is re-stamped with the epoch it was requested at, so
    the epoch bookkeeping under test is the real one and not the fixture's.
    """
    seen = []

    async def fake_snapshot(page, epoch, contract, step_key):
        spec = plan[min(len(seen), len(plan) - 1)]
        seen.append((epoch, contract, step_key))
        return replace(spec, epoch=epoch)

    monkeypatch.setattr(drive_mod, "snapshot", fake_snapshot)
    return seen


def _deps(page, audit, **kw):
    kw.setdefault("contract_name", "payer_enrollment")
    return Deps(page=page, audit=audit, vault=CredentialVault({}), scrubber=Scrubber(),
                store=kw.pop("store", None), oracle=kw.pop("oracle", None),
                ctx_holder=kw.pop("ctx_holder", CtxHolder()), grant=FULL, **kw)


def _audit(tmp_path):
    return AuditLog(tmp_path / "audit.jsonl", run_id="test-run")


FIX_ID = "fix-abc"


# --- Ruling R2(a) and R12: the memory path ---


async def test_a_two_action_memory_replay_passes_the_guard_on_the_submit(
        monkeypatch, tmp_path):
    """Ruling R2(a). drive() re-perceives between actions, so by action 2 the
    observation is at a later epoch than the step entry. Without re-stamping the
    baseline onto each fresh observation the guard refuses every multi-action
    tier-3 fix, and the flagship tick-then-submit heal can never be replayed."""
    _install_snapshot(monkeypatch, [ENTRY_OBS, ENTRY_OBS, DONE_OBS])
    page, audit = FakePage(), _audit(tmp_path)
    deps = _deps(page, audit)
    entry = replace(ENTRY_OBS, epoch=deps.next_epoch())
    ctx = ActionContext(step=STEP3, grant=FULL, observation=entry,
                        baseline=Baseline(reading=OracleReading(True, False, 0, None, {}),
                                          epoch=entry.epoch),
                        source="memory:" + FIX_ID)
    driver = _MemoryDriver(actions=list(replay(FakeFix(), {})))

    verdict = await drive(driver, STEP3, ctx, deps)

    assert verdict is PageVerdict.PASSED
    assert page.calls == [("click", "#reviewed"), ("click", "#confirm-and-submit")]
    assert len(deps.ctx_holder.trace) == 2


async def test_the_memory_path_stamps_the_fix_id_onto_every_audited_action(
        monkeypatch, tmp_path):
    """Ruling R12. The audit is the only evidence that a warm run reused memory
    rather than resolving again, so provenance has to reach the action record."""
    _install_snapshot(monkeypatch, [ENTRY_OBS, ENTRY_OBS, DONE_OBS])
    page, audit = FakePage(), _audit(tmp_path)
    deps = _deps(page, audit)
    entry = replace(ENTRY_OBS, epoch=deps.next_epoch())
    ctx = ActionContext(step=STEP3, grant=FULL, observation=entry,
                        baseline=Baseline(reading=OracleReading(True, False, 0, None, {}),
                                          epoch=entry.epoch),
                        source="memory:" + FIX_ID)

    await drive(_MemoryDriver(actions=list(replay(FakeFix(), {}))), STEP3, ctx, deps)

    sources = [r["source"] for r in audit.records() if r["event"] == "action"]
    assert sources == ["memory:" + FIX_ID, "memory:" + FIX_ID]


async def test_an_outage_mid_replay_is_infrastructural_and_never_resolves(
        monkeypatch, tmp_path):
    """Spec 5.2. A missing target and a portal that stopped answering look
    identical to _find, and collapsing them is how an outage becomes a resubmit.
    Only page_verify can tell them apart, so a miss must still be classified
    rather than short-circuited into MECHANICAL."""
    gone = Observation(url="http://portal/enroll", epoch=0, elements=[],
                       text="Service unavailable", fingerprint="fp-error")
    _install_snapshot(monkeypatch, [ENTRY_OBS, gone, gone])
    page, audit = FakePage(), _audit(tmp_path)
    deps = _deps(page, audit)
    deps.last_http_status = 503
    entry = replace(ENTRY_OBS, epoch=deps.next_epoch())
    ctx = ActionContext(step=STEP3, grant=FULL, observation=entry,
                        baseline=Baseline(reading=OracleReading(True, False, 0, None, {}),
                                          epoch=entry.epoch),
                        source="memory:" + FIX_ID)

    verdict = await drive(_MemoryDriver(actions=list(replay(FakeFix(), {}))),
                          STEP3, ctx, deps)

    assert verdict is PageVerdict.INFRASTRUCTURAL
    assert page.calls == [("click", "#reviewed")], "action 1 landed, action 2 did not"
    assert deps.last_observation is not None, \
        "an abandoned replay must still leave the observation run_step reads"
    assert deps.last_observation.text == "Service unavailable"


async def test_a_missing_target_with_a_healthy_page_is_still_a_mechanical_miss(
        monkeypatch, tmp_path):
    """The true-miss case stays pinned: the portal answered, the element is gone."""
    gone = Observation(url="http://portal/enroll", epoch=0, elements=[],
                       text="Enrollment form", fingerprint="fp-other")
    _install_snapshot(monkeypatch, [ENTRY_OBS, gone, gone])
    page, audit = FakePage(), _audit(tmp_path)
    deps = _deps(page, audit)
    deps.last_http_status = 200
    entry = replace(ENTRY_OBS, epoch=deps.next_epoch())
    ctx = ActionContext(step=STEP3, grant=FULL, observation=entry,
                        baseline=Baseline(reading=OracleReading(True, False, 0, None, {}),
                                          epoch=entry.epoch),
                        source="memory:" + FIX_ID)

    verdict = await drive(_MemoryDriver(actions=list(replay(FakeFix(), {}))),
                          STEP3, ctx, deps)

    assert verdict is PageVerdict.MECHANICAL


async def test_an_abandoned_replay_is_never_reported_as_a_pass(monkeypatch, tmp_path):
    """Spec 6.6: a drift mismatch degrades to a MISS. A miss that advances the
    step is not a miss.

    Four of the five steps in the shipped contract declare no postconditions at
    all, and page_verify with nothing to check answers PASSED. So classifying an
    abandoned replay purely by the page would report a stale fix on
    provider.open as CONFIRMED and walk on to the next step against whatever page
    happened to be open, instead of healing. The page steers; it never decides
    that an act it never saw succeeded.
    """
    step1 = Step(step_key="provider.open", intent="open the record", tier=1)
    gone = Observation(url="http://portal/index", epoch=0, elements=[],
                       text="Provider index", fingerprint="fp-other")
    _install_snapshot(monkeypatch, [gone, gone])
    page, audit = FakePage(), _audit(tmp_path)
    deps = _deps(page, audit)
    entry = replace(ENTRY_OBS, epoch=deps.next_epoch())
    ctx = ActionContext(step=step1, grant=FULL, observation=entry, baseline=None,
                        source="memory:" + FIX_ID)

    verdict = await drive(_MemoryDriver(actions=list(replay(FakeFix(), {}))),
                          step1, ctx, deps)

    assert verdict is PageVerdict.MECHANICAL
    assert page.calls == []


async def test_an_abandoned_replay_still_yields_to_a_stated_refusal(
        monkeypatch, tmp_path):
    """A refusal is more specific than a miss and carries its reason forward, so
    it must survive the downgrade that only ever applies to PASSED."""
    bounced = Observation(url="http://portal/enroll", epoch=0, elements=[],
                          text=REFUSAL_TEXT, fingerprint="fp-other")
    _install_snapshot(monkeypatch, [bounced, bounced])
    page, audit = FakePage(), _audit(tmp_path)
    deps = _deps(page, audit)
    entry = replace(ENTRY_OBS, epoch=deps.next_epoch())
    ctx = ActionContext(step=STEP3, grant=FULL, observation=entry,
                        baseline=Baseline(reading=OracleReading(True, False, 0, None, {}),
                                          epoch=entry.epoch),
                        source="memory:" + FIX_ID)

    verdict = await drive(_MemoryDriver(actions=list(replay(FakeFix(), {}))),
                          STEP3, ctx, deps)

    assert verdict is PageVerdict.REJECTED


async def test_a_fix_whose_target_is_gone_degrades_to_a_miss_and_never_forces(
        monkeypatch, tmp_path):
    """Spec 6.6: a stale fix is never forced. The page here has neither stored
    identity, so drive must report a mechanical miss with no side effect."""
    empty = Observation(url="http://portal/enroll", epoch=0, elements=[],
                        text="Enrollment form", fingerprint="fp-other")
    _install_snapshot(monkeypatch, [empty])
    page, audit = FakePage(), _audit(tmp_path)
    deps = _deps(page, audit)
    entry = replace(ENTRY_OBS, epoch=deps.next_epoch())
    ctx = ActionContext(step=STEP3, grant=FULL, observation=entry,
                        baseline=Baseline(reading=OracleReading(True, False, 0, None, {}),
                                          epoch=entry.epoch),
                        source="memory:" + FIX_ID)

    verdict = await drive(_MemoryDriver(actions=list(replay(FakeFix(), {}))),
                          STEP3, ctx, deps)

    assert verdict is PageVerdict.MECHANICAL
    assert page.calls == []


# --- Ruling R2(b): the session path's refresh contract ---


async def test_refresh_settles_re_perceives_and_re_stamps_the_baseline(
        monkeypatch, tmp_path):
    """Ruling R2(b). Task 11's tool handler calls this after every execute. It must
    hand back a fresh observation AND leave ctx_holder.current usable for the next
    action, or the second tool call of any tier-3 step is refused."""
    _install_snapshot(monkeypatch, [DONE_OBS])
    page, audit = FakePage(), _audit(tmp_path)
    holder = CtxHolder()
    deps = _deps(page, audit, ctx_holder=holder)
    entry = replace(ENTRY_OBS, epoch=deps.next_epoch())
    reading = OracleReading(True, False, 0, None, {})
    holder.bind(deps, STEP3)
    holder.current = ActionContext(step=STEP3, grant=FULL, observation=entry,
                                   baseline=Baseline(reading=reading, epoch=entry.epoch),
                                   source="cold")
    holder.set_observation(entry)

    fresh = await holder.refresh()

    assert page.settles == 1, "refresh must settle before it perceives"
    assert fresh.epoch > entry.epoch
    assert holder.current.observation is fresh
    assert holder.current.baseline.epoch == fresh.epoch
    assert holder.current.baseline.reading is reading, "the READING is never re-read"
    assert holder.current.step is STEP3 and holder.current.source == "cold"
    holder.record("next-action")
    assert holder.trace == [(fresh.fingerprint, "next-action")]


async def test_refresh_carries_a_step_with_no_baseline(monkeypatch, tmp_path):
    """A tier-1 or tier-2 step holds no baseline, and refresh must not invent one."""
    _install_snapshot(monkeypatch, [ENTRY_OBS])
    page, audit = FakePage(), _audit(tmp_path)
    holder = CtxHolder()
    deps = _deps(page, audit, ctx_holder=holder)
    step1 = Step(step_key="provider.open", intent="open the record", tier=1)
    holder.bind(deps, step1)
    holder.current = ActionContext(step=step1, grant=FULL, observation=ENTRY_OBS,
                                   baseline=None, source="cold")
    holder.set_observation(ENTRY_OBS)

    await holder.refresh()

    assert holder.current.baseline is None


# --- run_step: the loop, the capture path, and the rulings it carries ---

NPI = "1700000001"
PAYER = "Aetna"
BINDINGS = {"npi": NPI, "payer": PAYER}

CONTRACT = Contract.model_validate({
    "contract": "payer_enrollment",
    "version": 3,
    "site": "enrollment_portal",
    "goal": "enroll a provider and confirm it posted",
    "oracle": {"kind": "http_json", "url": "{base}/api/sor/enrollment/{npi}",
               "strength": "cross_system"},
    "identity": {"key": ["npi", "payer"], "resolve_ambiguity_by": "oracle"},
    "steps": [STEP3.model_dump()],
    "pii": {"redact": [], "never_screenshot_urls": []},
})

ABSENT = OracleReading(reachable=True, enrolled=False, count=0, latest=None,
                       raw={"count": 0})
POSTED = OracleReading(
    reachable=True, enrolled=True, count=1,
    latest={"npi": NPI, "payer": PAYER, "confirmation_id": "PC-000123"},
    raw={"count": 1},
)


class FakeOracle:
    """Reads are consumed in order: baseline first, then the after-read."""

    def __init__(self, readings, table=None):
        self._readings = list(readings)
        self._table = list(table or [])
        self.reads = 0

    async def read(self, npi):
        reading = self._readings[min(self.reads, len(self._readings) - 1)]
        self.reads += 1
        return reading

    async def read_all(self):
        return list(self._table)


def _acting_session(steps_to_take, spy=None):
    """Stand in for the resolution session.

    It acts exactly as Task 11's tool handler does, so the trace, the epochs and
    the refresh contract under test are the real ones: execute, record, refresh.
    """
    async def fake_run_resolution(step, obs, ctx, negatives, deps, failure_context=None):
        if spy is not None:
            spy["negatives"] = negatives
            spy["failure_context"] = failure_context
            spy["calls"] = spy.get("calls", 0) + 1
        plan = (steps_to_take(failure_context) if callable(steps_to_take)
                else steps_to_take)
        for kind, element_id in plan:
            live = deps.ctx_holder.current
            target = next(e for e in live.observation.elements
                          if e.element_id == element_id)
            action = Action(kind=kind, target_id=target.target_id, value=None,
                            step_key=step.step_key, epoch=live.observation.epoch)
            await execute(action, live, deps.page, deps.audit, deps.vault, deps.scrubber)
            deps.ctx_holder.record(action)
            await deps.ctx_holder.refresh()

    return fake_run_resolution


def _cold_run(monkeypatch, tmp_path, plan, readings, table=None,
              steps_to_take=(("click", "reviewed"), ("submit", "confirm-and-submit")),
              spy=None, store=None):
    """Wire one cold step: scripted perception, a scripted oracle, a real store."""
    _install_snapshot(monkeypatch, plan)
    monkeypatch.setattr("vba.resolve.session.run_resolution",
                        _acting_session(steps_to_take, spy))
    page, audit = FakePage(), _audit(tmp_path)
    deps = _deps(page, audit, store=store or FixStore(tmp_path / "memory.db"),
                 oracle=FakeOracle(readings, table))
    return page, audit, deps


async def test_a_cold_confirmed_step_captures_a_promoted_fix(monkeypatch, tmp_path):
    """Rulings R3 and R4, and spec 6.3, 6.4.

    The heal the demo turns on: a session ticks a newly required checkbox and then
    submits, the record store confirms, and the SEQUENCE is written to memory so
    the next entity does not resolve the same page again. It must be promoted, or
    lookup ignores it and memory is decorative.
    """
    page, audit, deps = _cold_run(
        monkeypatch, tmp_path,
        plan=[ENTRY_OBS, ENTRY_OBS, DONE_OBS, DONE_OBS],
        readings=[ABSENT, POSTED],
    )

    outcome = await run_step(STEP3, CONTRACT, BINDINGS, 0, deps)

    assert outcome.outcome is Outcome.CONFIRMED
    assert outcome.source == "cold"
    assert outcome.verif_strength == "cross_system"

    fix = deps.store.lookup(CONTRACT.site, CONTRACT.name, STEP3.step_key)
    assert fix is not None, "a confirmed cold heal that stores nothing never heals"
    assert fix.provenance == "eval_promoted"
    assert [a.identity_id for a in fix.actions] == ["reviewed", "confirm-and-submit"]
    assert fix.actions[-1].is_submit is True
    assert fix.action_tier == 3
    assert fix.match_mode == "exact_identity"
    assert fix.page_fingerprint == ENTRY_OBS.fingerprint
    assert any(r["event"] == "memory_write" for r in audit.records())


async def test_the_captured_fix_stores_parameters_as_references_not_literals(
        monkeypatch, tmp_path):
    """Spec 6.1, closing Task 10's coverage gap end to end.

    An identity carrying this provider's NPI must be stored templated. Stored
    literally it would re-bind successfully to the WRONG provider on any page that
    lists them all, and every later step would act on the wrong record.
    """
    named = _elements(submit_name="Confirm and submit enrollment for " + NPI)
    entry = replace(ENTRY_OBS, elements=named)
    done = replace(DONE_OBS, elements=named)
    page, audit, deps = _cold_run(
        monkeypatch, tmp_path,
        plan=[entry, entry, done, done],
        readings=[ABSENT, POSTED],
    )

    await run_step(STEP3, CONTRACT, BINDINGS, 0, deps)

    fix = deps.store.lookup(CONTRACT.site, CONTRACT.name, STEP3.step_key)
    assert fix.actions[-1].identity_name == "Confirm and submit enrollment for {npi}"


async def test_a_candidate_whose_identity_cannot_re_bind_is_never_promoted(
        monkeypatch, tmp_path):
    """Ruling R3: the eval gate is a deterministic replay over the recording.

    This identity carries a literal brace token, so templating leaves it alone and
    binding at reuse time rewrites it into a string the page never had. The
    candidate cannot resolve against the very observation it was captured from, so
    it is written inert rather than promoted. No model judges this.
    """
    lossy = _elements(checkbox_name="Reviewed for {npi}")
    entry = replace(ENTRY_OBS, elements=lossy)
    done = replace(DONE_OBS, elements=lossy)
    page, audit, deps = _cold_run(
        monkeypatch, tmp_path,
        plan=[entry, entry, done, done],
        readings=[ABSENT, POSTED],
    )

    outcome = await run_step(STEP3, CONTRACT, BINDINGS, 0, deps)

    assert outcome.outcome is Outcome.CONFIRMED       # the run itself still confirmed
    assert deps.store.lookup(CONTRACT.site, CONTRACT.name, STEP3.step_key) is None
    written = deps.store.current_positive(CONTRACT.site, CONTRACT.name, STEP3.step_key)
    assert written is not None and written.provenance == "candidate"


async def test_capturing_over_an_existing_fix_emits_a_supersede_event(
        monkeypatch, tmp_path):
    """Ruling R4, spec 8.1. write_candidate stamps valid_to silently, and a
    read-only action log cannot prove a fix was replaced. The event is the
    evidence that supersede-on-drift happened."""
    store = FixStore(tmp_path / "memory.db")
    old = LearnedFix.new(site=CONTRACT.site, contract=CONTRACT.name,
                         step_key=STEP3.step_key, intent=STEP3.intent,
                         page_fingerprint="fp-layout-A", actions=list(FIX_ACTIONS),
                         match_mode="exact_identity", action_tier=3,
                         provenance="eval_promoted")
    store.write_candidate(old)

    page, audit, deps = _cold_run(
        monkeypatch, tmp_path,
        plan=[ENTRY_OBS, ENTRY_OBS, DONE_OBS, DONE_OBS],
        readings=[ABSENT, POSTED], store=store,
    )
    await run_step(STEP3, CONTRACT, BINDINGS, 0, deps)

    new = store.lookup(CONTRACT.site, CONTRACT.name, STEP3.step_key)
    events = [r for r in audit.records() if r["event"] == "memory_superseded"]
    assert len(events) == 1
    assert events[0]["old_fix_id"] == old.fix_id
    assert events[0]["new_fix_id"] == new.fix_id
    assert "fingerprint" in events[0]["reason"]


# --- Ruling R7: the confirmation number ---


def test_the_confirmation_number_is_extracted_from_the_page_text():
    assert _extract_confirmation("Submitted successfully. Confirmation number: "
                                 "PC-000123") == "PC-000123"


def test_a_page_with_no_confirmation_number_yields_none():
    assert _extract_confirmation("Submitted successfully.") is None
    assert _extract_confirmation("") is None


async def test_a_success_page_that_shows_no_confirmation_number_is_a_discrepancy(
        monkeypatch, tmp_path):
    """Ruling R7 made load-bearing by ruling R15 (Task 7): CONFIRMED needs three
    agreements, and the third is the number on the page appearing in the record.
    A run that never extracts it would report every posted enrollment as a
    discrepancy, so this pins that extraction actually happens."""
    silent = replace(DONE_OBS, text="Submitted successfully.")
    page, audit, deps = _cold_run(
        monkeypatch, tmp_path,
        plan=[ENTRY_OBS, ENTRY_OBS, silent, silent],
        readings=[ABSENT, POSTED],
    )

    outcome = await run_step(STEP3, CONTRACT, BINDINGS, 0, deps)

    assert deps.page_confirmation() is None
    assert outcome.outcome is Outcome.DISCREPANCY
    assert deps.store.lookup(CONTRACT.site, CONTRACT.name, STEP3.step_key) is None


# --- Ruling R8: refusal context and negative entries ---


async def test_a_stated_refusal_writes_a_negative_entry_and_keeps_its_reason(
        monkeypatch, tmp_path):
    """Spec 5.2, 6.3. The refusal text is the payload: it is what stops the next
    entity rediscovering the same rejection, and what the next attempt is told."""
    bounced = replace(ENTRY_OBS, text=REFUSAL_TEXT)
    page, audit, deps = _cold_run(
        monkeypatch, tmp_path,
        plan=[ENTRY_OBS, bounced, bounced],
        readings=[ABSENT, ABSENT],
        steps_to_take=(("submit", "confirm-and-submit"),),
    )

    outcome = await run_step(STEP3, CONTRACT, BINDINGS, 0, deps)

    assert outcome.outcome is Outcome.REJECTED
    negatives = deps.store.negatives_for(CONTRACT.site, CONTRACT.name, STEP3.step_key)
    assert len(negatives) == 1
    assert negatives[0].failure_mode == "Please confirm you have reviewed"
    assert negatives[0].polarity == "negative"
    assert deps.failure_context == "Please confirm you have reviewed"
    assert any(r["event"] == "memory_write" for r in audit.records())


def test_the_refusal_detail_prefers_the_contracts_own_words():
    """The postcondition string is authored, bounded and free of page content,
    so it is the better payload whenever one fired."""
    bounced = replace(ENTRY_OBS, text=REFUSAL_TEXT)
    assert drive_mod._refusal_detail(STEP3, bounced, Scrubber()) == \
        "Please confirm you have reviewed"


def test_a_refusal_excerpt_is_bounded_and_scrubbed():
    """The refusal text is persisted as a negative entry AND rendered into the
    next resolution's prompt by render_task, which does not scrub. A secret that
    the page echoed back must not reach either."""
    scrubber = Scrubber()
    scrubber.record("Staging2026!")
    step = Step(step_key="portal.login", intent="sign in", tier=2)
    echoed = replace(ENTRY_OBS, text="Sign-in failed for Staging2026! " + "x" * 500)

    detail = drive_mod._refusal_detail(step, echoed, scrubber)

    assert "Staging2026!" not in detail
    assert len(detail) <= 200


async def test_the_next_attempt_is_told_why_the_last_one_was_refused(
        monkeypatch, tmp_path):
    """Rulings R8 and R18. A resolution that is not told the refusal repeats it,
    and the negative entries have a read path or they are pointless."""
    store = FixStore(tmp_path / "memory.db")
    store.write_candidate(LearnedFix.new(
        site=CONTRACT.site, contract=CONTRACT.name, step_key=STEP3.step_key,
        intent=STEP3.intent, page_fingerprint=ENTRY_OBS.fingerprint, actions=[],
        match_mode="exact_identity", action_tier=3, polarity="negative",
        failure_mode="submitting without ticking the review box"))
    spy = {}
    page, audit, deps = _cold_run(
        monkeypatch, tmp_path,
        plan=[ENTRY_OBS, ENTRY_OBS, DONE_OBS, DONE_OBS],
        readings=[ABSENT, POSTED], spy=spy, store=store)
    deps.failure_context = "Please confirm you have reviewed"

    await run_step(STEP3, CONTRACT, BINDINGS, 1, deps)

    assert spy["failure_context"] == "Please confirm you have reviewed"
    assert [n.failure_mode for n in spy["negatives"]] == [
        "submitting without ticking the review box"]


async def test_the_same_refusal_twice_leaves_exactly_one_negative_entry(
        monkeypatch, tmp_path):
    """Ruling R20. Every current negative is injected into every later resolution
    for this step, so duplicates do not merely waste rows: they crowd the prompt
    with the same warning repeated."""
    bounced = replace(ENTRY_OBS, text=REFUSAL_TEXT)
    page, audit, deps = _cold_run(
        monkeypatch, tmp_path,
        plan=[ENTRY_OBS, bounced, bounced, ENTRY_OBS, bounced, bounced],
        readings=[ABSENT],
        steps_to_take=(("submit", "confirm-and-submit"),))

    first = await run_step(STEP3, CONTRACT, BINDINGS, 0, deps)
    second = await run_step(STEP3, CONTRACT, BINDINGS, 1, deps)

    assert first.outcome is Outcome.REJECTED and second.outcome is Outcome.REJECTED
    negatives = deps.store.negatives_for(CONTRACT.site, CONTRACT.name, STEP3.step_key)
    assert [n.failure_mode for n in negatives] == ["Please confirm you have reviewed"]
    assert len([r for r in audit.records() if r["event"] == "memory_write"]) == 1


async def test_a_negative_entry_is_superseded_once_the_step_confirms(
        monkeypatch, tmp_path):
    """Spec 6.3, ruling R20: a portal fix must not leave a permanent blinder.

    Without this, the refusal learned on layout B is injected into every future
    resolution of this step forever, including on layouts where the approach it
    warns against is the correct one.
    """
    bounced = replace(ENTRY_OBS, text=REFUSAL_TEXT)

    def plan_for(failure_context):
        if failure_context is None:
            return (("submit", "confirm-and-submit"),)
        return (("click", "reviewed"), ("submit", "confirm-and-submit"))

    _install_snapshot(monkeypatch, [ENTRY_OBS, bounced, bounced,
                                    ENTRY_OBS, ENTRY_OBS, DONE_OBS, DONE_OBS])
    monkeypatch.setattr("vba.resolve.session.run_resolution",
                        _acting_session(plan_for))
    page, audit = FakePage(), _audit(tmp_path)
    deps = _deps(page, audit, store=FixStore(tmp_path / "memory.db"),
                 oracle=FakeOracle([ABSENT, ABSENT, ABSENT, POSTED]))

    result = await run_entity(CONTRACT, BINDINGS, deps)

    assert result.terminal is Outcome.CONFIRMED
    assert deps.store.negatives_for(CONTRACT.site, CONTRACT.name,
                                    STEP3.step_key) == []
    healed = deps.store.lookup(CONTRACT.site, CONTRACT.name, STEP3.step_key)
    retired = [r for r in audit.records()
               if r["event"] == "memory_superseded" and r["reason"] == "approach succeeded"]
    assert len(retired) == 1
    assert retired[0]["new_fix_id"] == healed.fix_id, \
        "the supersede names the fix that replaced the failed approach"


async def test_a_confirmed_attempt_clears_the_previous_refusal(monkeypatch, tmp_path):
    """Stale refusal context is worse than none: it would be handed to a later
    step that was never refused."""
    page, audit, deps = _cold_run(
        monkeypatch, tmp_path,
        plan=[ENTRY_OBS, ENTRY_OBS, DONE_OBS, DONE_OBS],
        readings=[ABSENT, POSTED])
    deps.failure_context = "an earlier step's refusal"

    await run_step(STEP3, CONTRACT, BINDINGS, 1, deps)

    assert deps.failure_context is None


# --- The memory branch of run_step ---


async def test_a_promoted_fix_that_still_resolves_is_replayed_without_a_session(
        monkeypatch, tmp_path):
    """Spec 7.2's reuse beat. The point of memory is that the warm run does not
    spawn a resolution session for this step at all."""
    store = FixStore(tmp_path / "memory.db")
    fix = LearnedFix.new(site=CONTRACT.site, contract=CONTRACT.name,
                         step_key=STEP3.step_key, intent=STEP3.intent,
                         page_fingerprint=ENTRY_OBS.fingerprint,
                         actions=list(FIX_ACTIONS), match_mode="exact_identity",
                         action_tier=3, provenance="eval_promoted")
    store.write_candidate(fix)

    async def never(*a, **kw):
        raise AssertionError("a warm step must not spawn a resolution session")

    _install_snapshot(monkeypatch, [ENTRY_OBS, ENTRY_OBS, ENTRY_OBS, DONE_OBS])
    monkeypatch.setattr("vba.resolve.session.run_resolution", never)
    page, audit = FakePage(), _audit(tmp_path)
    deps = _deps(page, audit, store=store, oracle=FakeOracle([ABSENT, POSTED]))

    outcome = await run_step(STEP3, CONTRACT, BINDINGS, 0, deps)

    assert outcome.source == "memory:" + fix.fix_id
    assert outcome.outcome is Outcome.CONFIRMED
    assert page.calls == [("click", "#reviewed"), ("click", "#confirm-and-submit")]


async def test_a_fix_from_another_layout_is_detected_and_not_used(
        monkeypatch, tmp_path):
    """Spec 5.1: lookup is by step_key and the fingerprints are compared here, so
    drift produces a VISIBLE detection event instead of a silent miss."""
    store = FixStore(tmp_path / "memory.db")
    stale = LearnedFix.new(site=CONTRACT.site, contract=CONTRACT.name,
                           step_key=STEP3.step_key, intent=STEP3.intent,
                           page_fingerprint="fp-layout-A", actions=list(FIX_ACTIONS),
                           match_mode="exact_identity", action_tier=3,
                           provenance="eval_promoted")
    store.write_candidate(stale)
    page, audit, deps = _cold_run(
        monkeypatch, tmp_path,
        plan=[ENTRY_OBS, ENTRY_OBS, DONE_OBS, DONE_OBS],
        readings=[ABSENT, POSTED], store=store)

    outcome = await run_step(STEP3, CONTRACT, BINDINGS, 0, deps)

    assert outcome.source == "cold"
    detections = [r for r in audit.records() if r["event"] == "stale_fix_detected"]
    assert len(detections) == 1
    assert detections[0]["fix_id"] == stale.fix_id


async def test_memory_disabled_neither_reads_nor_writes(monkeypatch, tmp_path):
    """The memory-off demo beat has to be genuinely off, or the comparison it
    exists to make is not a comparison."""
    page, audit, deps = _cold_run(
        monkeypatch, tmp_path,
        plan=[ENTRY_OBS, ENTRY_OBS, DONE_OBS, DONE_OBS],
        readings=[ABSENT, POSTED])
    deps.memory_enabled = False
    deps.memory_writes_enabled = False

    await run_step(STEP3, CONTRACT, BINDINGS, 0, deps)

    assert deps.store.current_positive(CONTRACT.site, CONTRACT.name,
                                       STEP3.step_key) is None


# --- Ruling R5: the identity the oracle is asked about ---


def test_the_expected_identity_is_the_contracts_key_over_the_bindings():
    assert _identity(CONTRACT, BINDINGS) == {"npi": NPI, "payer": PAYER}


def test_a_missing_identity_component_is_omitted_rather_than_raising():
    """Ruling R5: the CLI supplies payer, and a KeyError here would take down a
    run over a binding that adjudicate can simply not check."""
    assert _identity(CONTRACT, {"npi": NPI}) == {"npi": NPI}


# --- Ruling R13: the http status the infrastructural verdict depends on ---


class FakeResponse:
    def __init__(self, page, status, resource_type="document"):
        self.status = status
        self.frame = page.main_frame
        self.request = type("R", (), {"resource_type": resource_type})()


async def test_the_response_listener_is_attached_once_per_page(monkeypatch, tmp_path):
    page, audit, deps = _cold_run(
        monkeypatch, tmp_path,
        plan=[ENTRY_OBS, ENTRY_OBS, DONE_OBS, DONE_OBS],
        readings=[ABSENT, POSTED])

    await run_step(STEP3, CONTRACT, BINDINGS, 0, deps)
    await run_step(STEP3, CONTRACT, BINDINGS, 1, deps)

    assert [e for e, _ in page.handlers] == ["response"]


def test_the_listener_records_the_main_document_status_only():
    """Spec 5.2: a 5xx is the infrastructural verdict, which never routes to
    resolution. A 500 on an image is not a portal outage."""
    page = FakePage()
    deps = _deps(page, audit=None)
    deps.attach_response_listener()
    handler = page.handlers[0][1]

    handler(FakeResponse(page, 503))
    assert deps.last_http_status == 503

    handler(FakeResponse(page, 500, resource_type="image"))
    assert deps.last_http_status == 503


def test_a_listener_that_raises_can_never_abort_an_action():
    page = FakePage()
    deps = _deps(page, audit=None)
    deps.attach_response_listener()
    page.handlers[0][1](object())          # nothing a Response has
    assert deps.last_http_status is None


# --- Acting on a baseline that cannot support a verdict ---


async def test_an_already_enrolled_provider_is_never_submitted_again(
        monkeypatch, tmp_path):
    """Spec 5.3: baseline already enrolled means ALREADY_SATISFIED and never
    submit. Adjudicating after acting would be too late; the act is irreversible."""
    enrolled = OracleReading(reachable=True, enrolled=True, count=1,
                             latest={"npi": NPI}, raw={"count": 1})
    page, audit, deps = _cold_run(
        monkeypatch, tmp_path, plan=[ENTRY_OBS], readings=[enrolled])

    outcome = await run_step(STEP3, CONTRACT, BINDINGS, 0, deps)

    assert outcome.outcome is Outcome.ALREADY_SATISFIED
    assert page.calls == [], "nothing may be submitted for an enrolled provider"


async def test_an_unreachable_baseline_never_acts(monkeypatch, tmp_path):
    """Spec 5.5: an act whose outcome could never be adjudicated is pure harm.
    Unknown is not absent, and a retry on an unknown duplicates."""
    unknown = OracleReading(reachable=False, enrolled=False, count=0, latest=None,
                            raw=None)
    page, audit, deps = _cold_run(
        monkeypatch, tmp_path, plan=[ENTRY_OBS], readings=[unknown])

    outcome = await run_step(STEP3, CONTRACT, BINDINGS, 0, deps)

    assert outcome.outcome is Outcome.UNVERIFIABLE
    assert page.calls == []


# --- A step with no oracle predicate ---


# --- The seam Task 12 left open: run/machine.py drives run_step ---


async def test_the_machine_re_enters_a_refused_step_with_its_reason_and_confirms(
        monkeypatch, tmp_path):
    """Spec 5.1, 5.2, 6.3, end to end through run_entity.

    The whole heal in one test: a first attempt submits without the newly required
    tick and is refused, the refusal is written as a negative entry AND handed to
    the next attempt, the second attempt ticks and submits, the record store
    confirms, and the sequence is captured for the next entity. run/ owns the
    re-entry, which is why run_step returns an outcome instead of recursing.
    """
    bounced = replace(ENTRY_OBS, text=REFUSAL_TEXT)
    seen = []

    def plan_for(failure_context):
        seen.append(failure_context)
        if failure_context is None:
            return (("submit", "confirm-and-submit"),)
        return (("click", "reviewed"), ("submit", "confirm-and-submit"))

    _install_snapshot(monkeypatch, [ENTRY_OBS, bounced, bounced,
                                    ENTRY_OBS, ENTRY_OBS, DONE_OBS, DONE_OBS])
    spy = {}
    monkeypatch.setattr("vba.resolve.session.run_resolution",
                        _acting_session(plan_for, spy))
    page, audit = FakePage(), _audit(tmp_path)
    deps = _deps(page, audit, store=FixStore(tmp_path / "memory.db"),
                 oracle=FakeOracle([ABSENT, ABSENT, ABSENT, POSTED]))

    result = await run_entity(CONTRACT, BINDINGS, deps)

    assert [o.outcome for o in result.outcomes] == [Outcome.REJECTED,
                                                    Outcome.CONFIRMED]
    assert result.terminal is Outcome.CONFIRMED
    assert result.escalated is False
    assert seen == [None, "Please confirm you have reviewed"]
    assert spy["negatives"][0].failure_mode == "Please confirm you have reviewed"
    fix = deps.store.lookup(CONTRACT.site, CONTRACT.name, STEP3.step_key)
    assert [a.identity_id for a in fix.actions] == ["reviewed", "confirm-and-submit"]


async def test_a_step_without_satisfied_when_is_judged_on_the_page_alone(
        monkeypatch, tmp_path):
    """Spec 5.1: the baseline read is gated on satisfied_when, so a tier-1 step
    reads no oracle and reports on_page strength honestly."""
    step1 = Step(step_key="provider.open", intent="open the record", tier=1,
                 postconditions=[Postcondition(text_present="Provider record")])
    found = replace(ENTRY_OBS, text="Provider record for Dr. Santos")
    _install_snapshot(monkeypatch, [ENTRY_OBS, ENTRY_OBS, found, found])
    monkeypatch.setattr("vba.resolve.session.run_resolution",
                        _acting_session((("click", "reviewed"),)))
    page, audit = FakePage(), _audit(tmp_path)
    oracle = FakeOracle([ABSENT])
    deps = _deps(page, audit, store=FixStore(tmp_path / "memory.db"), oracle=oracle)

    outcome = await run_step(step1, CONTRACT, BINDINGS, 0, deps)

    assert outcome.outcome is Outcome.CONFIRMED
    assert outcome.verif_strength == "on_page"
    assert oracle.reads == 0, "a step with no satisfied_when must not read the oracle"
