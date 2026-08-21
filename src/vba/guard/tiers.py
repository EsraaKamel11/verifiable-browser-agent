from vba.act.actions import Action, ActionContext


class GuardRefusal(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def check(action: Action, ctx: ActionContext) -> None:
    """Spec 4.3. Called inside execute(), the only path to a side effect.

    Memory-originated and model-originated actions traverse this identically:
    the guard cannot tell them apart, and that is the point.
    """
    obs = ctx.observation

    if action.epoch != obs.epoch:
        raise GuardRefusal(
            "action carries epoch " + str(action.epoch) + " but the current "
            "observation is epoch " + str(obs.epoch) + "; refusing rather than "
            "re-binding a stale target id"
        )

    try:
        element = obs.by_id(action.target_id)
    except KeyError:
        raise GuardRefusal(
            "no element with target id " + str(action.target_id) + " in this observation"
        )

    # The shaping rule. What the resolver called the action does not decide its tier;
    # the element's own metadata does.
    #
    # The exemption spec 4.3 names ("an approved act would need an explicit
    # exemption rather than an implicit one"): a step the CONTRACT declares as
    # form-firing keeps its own tier. Without it the agent cannot log in to any
    # portal whose sign-in button is a submit control, which is every portal: the
    # rule exists to stop an unbaselined RECORD being posted, and an authentication
    # form posts no record. The exemption is declared per step, is off by default,
    # and does not touch the grant cap below, so a tier-1 grant still refuses it.
    exempt = element.is_submit and ctx.step.fires_form and ctx.step.tier < 3
    effective_tier = ctx.step.tier if exempt else (
        3 if element.is_submit else ctx.step.tier)

    if effective_tier > ctx.grant.max_tier:
        raise GuardRefusal(
            "tier " + str(effective_tier) + " exceeds the grant of tier "
            + str(ctx.grant.max_tier) + ": " + ctx.grant.reason
        )

    if element.is_submit and ctx.step.tier < 3 and not exempt:
        raise GuardRefusal(
            "element " + repr(element.name) + " is a submit control, but step "
            + repr(ctx.step.step_key) + " is tier " + str(ctx.step.tier)
            + "; a lower-tier step must not fire the form"
        )

    if effective_tier == 3:
        if ctx.baseline is None:
            raise GuardRefusal(
                "tier-3 act requires a baseline read taken before the first action "
                "of this step; none is held"
            )
        if ctx.baseline.epoch != obs.epoch:
            raise GuardRefusal(
                "baseline belongs to epoch " + str(ctx.baseline.epoch)
                + " but this step is at epoch " + str(obs.epoch)
                + "; a baseline from another step cannot authorize this act"
            )
