import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterator

from vba.act.actions import Action, ActionContext
from vba.act.choke import execute
from vba.guard.credentials import should_screenshot
from vba.guard.tiers import GuardRefusal
from vba.memory.capture import confidence, slice_capture, to_stored_actions
from vba.memory.store import LearnedFix, StoredAction
from vba.memory.templating import bind
from vba.oracle.delta import Baseline, Outcome, PageVerdict, adjudicate
from vba.perceive.snapshot import snapshot
from vba.verify.page import page_verify

from . import chaos
from .outcomes import StepOutcome


class RefusedReplay(GuardRefusal):
    """A stored action the guard refused, carrying what was refused. Ruling R22(a).

    A GuardRefusal names only its reason, and run_step is where the refusal has to
    be recorded and degraded, so the memory path re-raises with the action and the
    target attached. It stays a GuardRefusal, so nothing that already catches one
    changes behaviour.
    """

    def __init__(self, refusal: GuardRefusal, kind: str, target: str):
        super().__init__(refusal.reason)
        self.kind = kind
        self.target = target


class CtxHolder:
    """Carries the live ActionContext for the MCP tools, and records the trace that
    capture slices. Spec 6.3.

    Constructed with no arguments, because the CLI builds one per page before any
    step is known. run_step calls bind() to hand it the deps and step that
    refresh() needs.
    """

    def __init__(self):
        self.current = None
        self._fingerprint = ""
        self._observation = None
        self.trace: list[tuple[str, object]] = []
        # Parallel to trace, one entry per recorded action: the observation the
        # action was chosen from. Held in memory only and never persisted; it is
        # what _capture replays the candidate over (ruling R3).
        self.observations: list[object] = []
        self._deps = None
        self._step = None

    def bind(self, deps, step) -> None:
        """Ruling R2(b). refresh() needs the page, the epoch counter and the step
        key; run_step is the only place that knows all three."""
        self._deps = deps
        self._step = step

    def reset(self) -> None:
        """One trace per step attempt. An earlier attempt's actions are not part of
        this attempt's fix, and a bounce that leaves the fingerprint unchanged would
        otherwise let them survive the slice."""
        self.trace = []
        self.observations = []

    def set_observation_fingerprint(self, fp: str) -> None:
        self._fingerprint = fp

    def set_observation(self, obs) -> None:
        self._observation = obs
        self.set_observation_fingerprint(obs.fingerprint)

    def record(self, action) -> None:
        self.trace.append((self._fingerprint, action))
        self.observations.append(self._observation)

    async def refresh(self):
        """Settle, re-perceive, and re-stamp. Ruling R2(b); spec 5.1, 6.3.

        Task 11's tool handler calls this after every execute, so it does three
        jobs at once: it gives the model a page that is not stale, it advances the
        epoch the guard checks, and it moves the trace fingerprint forward so the
        NEXT recorded action carries the observation it was actually chosen from.

        The baseline READING is never re-read here. Only its epoch moves, because
        this is harness code re-affirming that the baseline taken before the first
        action still belongs to this step. A fresh read would defeat the delta.
        """
        deps, prev = self._deps, self.current
        await deps.settle()
        obs = await snapshot(deps.page, deps.next_epoch(), deps.contract_name,
                             self._step.step_key)
        self.current = ActionContext(
            step=prev.step,
            grant=prev.grant,
            observation=obs,
            baseline=_restamp(prev.baseline, obs.epoch),
            source=prev.source,
        )
        self.set_observation(obs)
        await self._capture(obs)
        return obs

    async def _capture(self, obs) -> None:
        """Demo tooling, off unless VBA_CAPTURE names a directory.

        It captures through should_screenshot, which the contract's
        never_screenshot_urls populates, so the auth pages are skipped rather than
        written and later deleted. Spec 4.4 names capture suppression as the
        load-bearing control on those pages, because the world's authenticator
        field is not a password input and renders its code in the clear.
        """
        out = os.environ.get("VBA_CAPTURE")
        if not out:
            return
        deps = self._deps
        pii = getattr(deps, "pii", None)
        if pii is not None and not should_screenshot(obs.url, pii):
            return
        directory = Path(out)
        directory.mkdir(parents=True, exist_ok=True)
        # The entity is in the name because every entity restarts the epoch
        # counter, so a multi-entity run would otherwise overwrite its own frames
        # and keep only the last provider's.
        entity = "-".join(str(v) for v in (getattr(deps, "bindings", {}) or {}).values())
        name = (entity + "-" if entity else "") \
            + str(obs.epoch).zfill(3) + "-" + self._step.step_key + ".png"
        try:
            await deps.page.screenshot(path=str(directory / name))
        except Exception:
            pass          # a capture failure must never affect the run


def _restamp(baseline, epoch: int):
    """Ruling R2(a). The guard demands baseline.epoch == observation.epoch, so a
    baseline that is not carried forward refuses the second action of every
    multi-action tier-3 fix."""
    return None if baseline is None else replace(baseline, epoch=epoch)


def replay(fix, bindings: dict[str, str]) -> Iterator[StoredAction]:
    """Bind this invocation's parameters into every stored string, in order."""
    for sa in fix.actions:
        yield replace(
            sa,
            identity_id=bind(sa.identity_id, bindings),
            identity_name=bind(sa.identity_name, bindings),
            value=bind(sa.value, bindings) if sa.value else None,
        )


@dataclass
class _MemoryDriver:
    """A bound fix, yielded one action at a time. Never a finished plan: the
    choke point and capture-slicing both need turn-by-turn acting (spec 5.1)."""

    actions: list
    kind = "memory"


@dataclass
class _SessionDriver:
    """A live resolution session. drive() only waits for it; the session acts
    through the granted MCP tools, and every one of those calls lands in the same
    execute() the memory path uses."""

    step: Any
    obs: Any
    ctx: Any
    deps: Any
    negatives: list = field(default_factory=list)
    failure_context: str | None = None
    kind = "session"

    async def run(self) -> None:
        # Imported here so that importing drive() does not drag the agent SDK and
        # a model client into the unit suite's process. Ruling R18: run_resolution
        # is the real entry point; the plan's resolve_session iterator never existed.
        from vba.resolve.session import run_resolution

        await run_resolution(self.step, self.obs, self.ctx, self.negatives,
                             self.deps, self.failure_context)


async def drive(driver, step, ctx, deps) -> PageVerdict:
    """One execution model for both paths. Spec 5.1.

    A memory replay yields StoredActions to be re-bound to the current epoch; a
    resolution session acts through the granted tools and this function only waits
    for it. Either way, every action crosses the choke point individually and the
    page is re-perceived between actions.
    """
    abandoned = False
    if driver.kind == "memory":
        for stored in driver.actions:
            obs = await snapshot(deps.page, deps.next_epoch(), deps.contract_name,
                                 step.step_key)
            target = _find(obs, stored)
            if target is None:
                # Degrade to a miss, never force (spec 6.6). But the classification
                # is page_verify's, not this loop's: a missing target and a portal
                # that stopped answering are indistinguishable here, and returning
                # MECHANICAL outright would send a 5xx outage into a resolution
                # session, which spec 5.2 forbids by name. Break, and let the
                # final snapshot below decide.
                abandoned = True
                break
            live_ctx = ActionContext(step=ctx.step, grant=ctx.grant, observation=obs,
                                     baseline=_restamp(ctx.baseline, obs.epoch),
                                     source=ctx.source)
            deps.ctx_holder.current = live_ctx
            deps.ctx_holder.set_observation(obs)
            action = Action(kind=stored.kind, target_id=target.target_id,
                            value=stored.value, step_key=step.step_key,
                            epoch=obs.epoch)
            try:
                await execute(action, live_ctx, deps.page, deps.audit, deps.vault,
                              deps.scrubber)
            except GuardRefusal as refusal:
                raise RefusedReplay(refusal, action.kind,
                                    target.element_id or target.name) from refusal
            deps.ctx_holder.record(action)
            await deps.settle()
    else:
        await driver.run()          # the session acts through the MCP tools

    final = await snapshot(deps.page, deps.next_epoch(), deps.contract_name,
                           step.step_key)
    deps.last_observation = final
    verdict = page_verify(step, final, deps.last_http_status)
    if abandoned and verdict is PageVerdict.PASSED:
        # Spec 6.6: a drift mismatch degrades to a MISS, and a miss that advances
        # the step is not a miss. page_verify with nothing to check answers PASSED,
        # so for any step that declares no postconditions a stale fix would
        # otherwise be reported as a success and the run would walk on instead of
        # healing. One of the five steps in the shipped contract is in that
        # position today (enrollment.select_payer, whose evidence is state and not
        # text), and any contract may add more, so this does not depend on the
        # count. Only PASSED is downgraded: an outage or a stated refusal is a more
        # specific answer than a miss and both must survive.
        return PageVerdict.MECHANICAL
    return verdict


def _find(obs, stored: StoredAction):
    """Exact identity, bound first. Spec 6.4: an id that survives with a changed
    accessible name is a miss, not a match."""
    for e in obs.elements:
        if (e.element_id == stored.identity_id
                and e.role == stored.identity_role
                and e.name == stored.identity_name):
            return e
    return None


_PAGE_VERDICT_TO_OUTCOME = {
    PageVerdict.PASSED: Outcome.CONFIRMED,
    PageVerdict.MECHANICAL: Outcome.NOT_ACTED,
    PageVerdict.REJECTED: Outcome.REJECTED,
    PageVerdict.INFRASTRUCTURAL: Outcome.VERIFIED_NOT_DONE,
}


def _page_to_outcome(page: PageVerdict) -> Outcome:
    """Only for a step with no satisfied_when. Where the contract binds a record
    predicate, the page never decides; adjudicate does (spec 5.2)."""
    return _PAGE_VERDICT_TO_OUTCOME[page]


def _identity(contract, bindings: dict[str, str]) -> dict[str, str]:
    """Ruling R5. The contract names which bindings identify the entity, so a
    record filed under a different one is MISFILED rather than confirmed."""
    return {k: bindings[k] for k in contract.identity.key if k in bindings}


CONFIRMATION_PATTERN = re.compile(r"PC-\d+")


def _extract_confirmation(text: str | None) -> str | None:
    """Ruling R7. Deterministic, because this is one of the three agreements
    CONFIRMED requires (spec 5.3) and a model reading it off the page would be a
    model deciding whether work posted."""
    match = CONFIRMATION_PATTERN.search(text or "")
    return match.group(0) if match else None


def _refusal_detail(step, obs, scrubber) -> str:
    """The stated reason, so the next attempt is not told merely that it failed.

    Scrubbed, because this string is both persisted as a negative entry and
    rendered into the next resolution's prompt by render_task, which does not
    scrub what it is handed (spec 4.4).
    """
    text = getattr(obs, "text", "") or ""
    for pc in step.postconditions:
        if pc.text_absent and pc.text_absent in text:
            return scrubber.clean(pc.text_absent)
    return scrubber.clean(" ".join(text.split()))[:200]


async def run_step(step, contract, bindings, attempts, deps) -> StepOutcome:
    """Spec 5.1, rendered as running code.

    It returns a typed outcome and never calls resolution inline: run/machine.py
    owns re-entry and the retry budget, so cross-invocation state is not hidden
    inside a recursive call.
    """
    # drive() and refresh() snapshot through deps.contract_name while the entry
    # snapshot below uses contract.name. They must agree or the entry fingerprint
    # matches nothing and capture slices an empty suffix forever.
    deps.contract_name = contract.name
    # The entity, so the resolution prompt can name it. Set every step because a
    # single Deps drives one entity and the value never changes within it, but a
    # value left unset would silently render a task with no parameters at all.
    deps.bindings = dict(bindings)
    deps.attach_response_listener()                      # ruling R13, once per page
    deps.ctx_holder.bind(deps, step)
    # Ruling R24, evaluation tooling: inert unless VBA_CHAOS names this step.
    await chaos.fire(chaos.PORTAL_DOWN_BEFORE, step.step_key)
    # One trace per attempt. An earlier attempt's refused actions are not part of
    # this attempt's fix, and a bounce that leaves the fingerprint unchanged would
    # otherwise carry them into the slice.
    deps.ctx_holder.reset()

    epoch = deps.next_epoch()
    obs = await snapshot(deps.page, epoch, contract.name, step.step_key)

    # Ruling R23. On a retry the memory lookup is skipped entirely. This loop only
    # runs again because the previous attempt failed, and replaying the identical
    # fix that just failed is the blinder spec 6.3 warns about: the stated refusal
    # can only reach a session, so a retry must resolve cold to make any use of it.
    # The fix is not superseded here; a later cold CONFIRMED supersedes it through
    # the capture path, which is where the evidence for that lives.
    fix = deps.store.lookup(contract.site, contract.name, step.step_key) \
        if deps.memory_enabled and attempts == 0 else None
    if fix and fix.page_fingerprint != obs.fingerprint:
        deps.audit.stale_fix_detected(fix.fix_id, fix.page_fingerprint, obs.fingerprint)
        fix = None

    baseline = None
    if step.satisfied_when:
        reading = await deps.oracle.read(bindings["npi"])
        baseline = Baseline(reading=reading, epoch=epoch)
        # Ruling R24. Fired here and not a line earlier: spec 7.3's case is a record
        # store that was readable when the act was authorized and unreadable when
        # the act needed confirming, which is the only ordering that produces a
        # genuinely unconfirmable submission.
        await chaos.fire(chaos.BLACKHOLE_AFTER_BASELINE, step.step_key)
        # Spec 5.3 and 5.5. Both of these are decided BEFORE acting, because both
        # answers are the same after acting and one of them is irreversible: an
        # already-enrolled provider must never be submitted again, and an act
        # whose outcome could never be adjudicated is pure harm.
        short_circuit = None
        if not reading.reachable:
            short_circuit = Outcome.UNVERIFIABLE
        elif reading.count > 0:
            short_circuit = Outcome.ALREADY_SATISFIED
        if short_circuit is not None:
            deps.audit.verification(step.step_key, short_circuit, reading.raw,
                                    reading.raw, entity=dict(bindings),
                                    page_confirmation=None)
            return StepOutcome(step.step_key, outcome=short_circuit, page=None,
                               source="cold", verif_strength="cross_system",
                               detail="no action was taken")

    entry_fingerprint = obs.fingerprint
    use_fix = bool(fix and fix.still_resolves(obs, bindings))
    source = "memory:" + fix.fix_id if use_fix else "cold"

    ctx = ActionContext(step=step, grant=deps.grant, observation=obs,
                        baseline=baseline, source=source)
    deps.ctx_holder.current = ctx
    deps.ctx_holder.set_observation(obs)

    if use_fix:
        driver = _MemoryDriver(actions=list(replay(fix, bindings)))
    else:
        negatives = deps.store.negatives_for(contract.site, contract.name,
                                             step.step_key) \
            if deps.memory_enabled else []
        driver = _SessionDriver(step, obs, ctx, deps, negatives,
                                deps.failure_context if attempts > 0 else None)

    try:
        page = await drive(driver, step, ctx, deps)
    except GuardRefusal as refusal:
        # Ruling R22(a). A refused replay is a MISS, never a crash: the guard
        # refusing a stored action is exactly the drift the memory path exists to
        # survive, and letting it escape run_entity would take the rest of the
        # batch down with it. The attempt is recorded, which CLAUDE.md already
        # promises, and MECHANICAL routes the step into a cold resolution.
        deps.audit.action_refused(step.step_key,
                                  kind=getattr(refusal, "kind", "unknown"),
                                  target=getattr(refusal, "target", ""),
                                  reason=refusal.reason, source=source)
        page = PageVerdict.MECHANICAL
        # No post-action observation exists. Leaving the previous step's would let
        # a confirmation number from an earlier page be read as this step's.
        deps.last_observation = None

    final = deps.last_observation

    # Overwritten on every step, including back to None. A refusal or a
    # confirmation left over from an earlier step is worse than none: it would be
    # handed to a resolution that was never refused, or agreed with by adjudicate
    # on a page that showed nothing.
    deps.failure_context = (_refusal_detail(step, final, deps.scrubber)
                            if page is PageVerdict.REJECTED else None)

    if not step.satisfied_when:
        outcome = _page_to_outcome(page)
        _write_negative(step, contract, bindings, entry_fingerprint, outcome,
                        source, deps)
        return StepOutcome(step.step_key, outcome=outcome, page=page,
                           source=source, verif_strength="on_page")

    deps.set_page_confirmation(_extract_confirmation(getattr(final, "text", "")))
    after = await deps.oracle.read(bindings["npi"])
    table = await deps.oracle.read_all() if page is PageVerdict.PASSED else []
    outcome = adjudicate(baseline, after, page, _identity(contract, bindings),
                         deps.page_confirmation(), table)
    deps.audit.verification(step.step_key, outcome, baseline.reading.raw, after.raw,
                            entity=dict(bindings),
                            page_confirmation=deps.page_confirmation())

    if outcome is Outcome.CONFIRMED and deps.memory_writes_enabled:
        captured = (_capture(step, contract, bindings, entry_fingerprint, deps)
                    if source == "cold" else None)
        _retire_negatives(step, contract, captured, deps)
    _write_negative(step, contract, bindings, entry_fingerprint, outcome,
                    source, deps)

    return StepOutcome(step.step_key, outcome=outcome, page=page, source=source,
                       verif_strength="cross_system")


def _sliced_trace(entry_fingerprint: str, holder) -> tuple[list, list]:
    """The captured suffix, paired with the observation each action was chosen
    from. slice_capture always returns a suffix of the trace, so the observations
    are the matching tail of the parallel list."""
    actions = slice_capture(entry_fingerprint, holder.trace)
    return actions, holder.observations[len(holder.trace) - len(actions):]


def _to_stored(actions: list, pre_obs: list, bindings: dict[str, str]) -> list:
    pairs = []
    for action, obs in zip(actions, pre_obs):
        try:
            element = obs.by_id(action.target_id)
        except (AttributeError, KeyError):
            continue                        # length mismatch fails validation below
        pairs.append((element, action.kind, action.value))
    return to_stored_actions(pairs, bindings)


def _replays_over_the_recording(stored: list, pre_obs: list,
                                bindings: dict[str, str]) -> bool:
    """Ruling R3, and the eval gate spec 6.4 describes.

    Deterministic replay of the candidate over the observations it was captured
    from, using the same bind-then-match-exactly predicate reuse will use. The
    oracle check backing promotion is the run's own CONFIRMED, which already
    happened. No model judges anything, and a live re-run is out of the question:
    it would file a second real record.
    """
    if not stored or len(stored) != len(pre_obs):
        return False
    for sa, obs in zip(stored, pre_obs):
        want_id = bind(sa.identity_id, bindings)
        want_name = bind(sa.identity_name, bindings)
        if not any(e.element_id == want_id and e.role == sa.identity_role
                   and e.name == want_name for e in obs.elements):
            return False
    return True


def _action_tier(step, stored: list) -> int:
    """The max tier across the sequence (spec 6.1), by the guard's own shaping
    rule: a submit control is tier 3 whatever the step says."""
    return 3 if any(sa.is_submit for sa in stored) else step.tier


def _capture(step, contract, bindings, entry_fingerprint: str, deps) -> str | None:
    """Spec 6.3, 6.4. Slice the trajectory, template it, gate it, write it.

    Returns the new fix's id, so the negatives this success retires can name what
    replaced them.
    """
    actions, pre_obs = _sliced_trace(entry_fingerprint, deps.ctx_holder)
    stored = _to_stored(actions, pre_obs, bindings)
    if not stored:
        # A zero-action fix would satisfy still_resolves vacuously and then
        # "resolve" a step by doing nothing at all.
        return None

    promote = _replays_over_the_recording(stored, pre_obs, bindings)
    fix = LearnedFix.new(
        site=contract.site, contract=contract.name, step_key=step.step_key,
        intent=step.intent, page_fingerprint=entry_fingerprint, actions=stored,
        match_mode="exact_identity", action_tier=_action_tier(step, stored),
        verif_strength="cross_system", trials=1, successes=1,
        confidence=confidence("cross_system", successes=1, trials=1, age_days=0),
    )
    # Ruling R4: read what this write is about to supersede BEFORE writing, since
    # write_candidate stamps valid_to on it as a side effect.
    superseded = deps.store.current_positive(contract.site, contract.name,
                                             step.step_key)
    deps.store.write_candidate(fix)                     # provenance: candidate
    if promote:
        deps.store.promote(fix.fix_id)                  # provenance: eval_promoted
    deps.audit.memory_write(fix.fix_id, step.step_key, entry_fingerprint)
    if superseded is not None:
        reason = ("fingerprint changed"
                  if superseded.page_fingerprint != entry_fingerprint
                  else "re-learned")
        deps.audit.memory_superseded(superseded.fix_id, fix.fix_id, reason)
    return fix.fix_id


def _retire_negatives(step, contract, new_fix_id: str | None, deps) -> None:
    """Spec 6.3, ruling R20. An entry is superseded once the step succeeds.

    Every current negative for a step is injected into every later resolution of
    it as an approach known to fail. One that outlives the failure it describes is
    a permanent blinder: after the portal is fixed, the agent is still told not to
    try the thing that now works. The step confirming is the evidence, and it is
    the same evidence promotion runs on, so no model judges this either.

    The event names the fix that replaced the failed approach when one was
    captured this run. On a warm confirmation there is no new fix to name, so the
    id is empty and the reason carries the meaning.
    """
    for negative in deps.store.negatives_for(contract.site, contract.name,
                                             step.step_key):
        deps.store.supersede(negative.fix_id)
        deps.audit.memory_superseded(negative.fix_id, new_fix_id or "",
                                     "approach succeeded")


def _write_negative(step, contract, bindings, entry_fingerprint: str,
                    outcome, source: str, deps) -> None:
    """Spec 6.3, ruling R8. The failed approach is written at the same moment it
    fails, so another entity does not rediscover the same rejection. The
    failure_mode text is the payload: it is what a later resolution is shown."""
    if outcome is not Outcome.REJECTED or source != "cold":
        return
    if not deps.memory_writes_enabled:
        return
    # Ruling R20. Negatives are read back as a list of approaches known to fail,
    # so a repeated refusal must not repeat the same warning in every later
    # prompt. There is no unique index on negative polarity, by design (a step may
    # carry several distinct current negatives), so the dedupe is done here.
    current = deps.store.negatives_for(contract.site, contract.name, step.step_key)
    if any(n.failure_mode == deps.failure_context for n in current):
        return
    actions, pre_obs = _sliced_trace(entry_fingerprint, deps.ctx_holder)
    stored = _to_stored(actions, pre_obs, bindings)
    fix = LearnedFix.new(
        site=contract.site, contract=contract.name, step_key=step.step_key,
        intent=step.intent, page_fingerprint=entry_fingerprint, actions=stored,
        match_mode="exact_identity", action_tier=_action_tier(step, stored),
        polarity="negative", failure_mode=deps.failure_context,
        verif_strength="cross_system" if step.satisfied_when else "on_page",
        trials=1, successes=0,
    )
    deps.store.write_candidate(fix)
    deps.audit.memory_write(fix.fix_id, step.step_key, entry_fingerprint)
