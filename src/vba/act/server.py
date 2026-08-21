from claude_agent_sdk import create_sdk_mcp_server, tool

from vba.act.actions import Action
from vba.act.choke import execute
from vba.guard.tiers import GuardRefusal
from vba.resolve.prompts import render_observation

SERVER_NAME = "actions"

READ_TOOLS = ["click", "fill", "select", "hover", "scroll", "navigate"]
WRITE_TOOLS = ["submit"]


def allowed_tools_for(step, grant) -> list[str]:
    """Spec 4.3 enforcement point 1. A tool the session was never granted cannot be
    called, which is the only available lever because this runtime has no
    forced-tool-selection parameter."""
    names = list(READ_TOOLS)
    if step.tier >= 3 and grant.max_tier >= 3 and 3 not in grant.propose_only_tiers:
        names += WRITE_TOOLS
    return ["mcp__" + SERVER_NAME + "__" + n for n in names]


def disallowed_tools_for(step, grant) -> list[str]:
    """Controller ruling R19, correcting this task's original enforcement choice.

    allowed_tools only auto-approves a tool call; per the installed SDK's own
    docs (types.py), the field that actually removes a tool from the model's
    context is disallowed_tools. Spec 4.3 enforcement point 1 requires genuine
    non-exposure, so every tool this step was not granted must be listed here.

    Computed as a set-difference against allowed_tools_for, so the granted and
    ungranted lists can never drift apart: there is exactly one source of truth
    (the tier/grant check in allowed_tools_for) and this function only negates it.
    """
    all_tools = ["mcp__" + SERVER_NAME + "__" + n for n in READ_TOOLS + WRITE_TOOLS]
    granted = set(allowed_tools_for(step, grant))
    return [t for t in all_tools if t not in granted]


def _target_label(ctx, action) -> str:
    """What the refusal was aimed at, for the audit record. An unknown target id is
    itself one of the refusal reasons, so this must not raise on the way out."""
    try:
        element = ctx.observation.by_id(action.target_id)
    except (KeyError, AttributeError):
        return "target_id=" + str(action.target_id)
    return element.element_id or element.name


def action_tools(ctx_holder, page, audit, vault, scrubber):
    """The tool list, before it is wrapped in a server.

    Exposed separately so the handler can be exercised directly by a unit test.
    The refusal path (ruling R22(b)) is the one branch a live model would reach
    only by accident, so it is the branch most in need of a deterministic test.

    Contract with ctx_holder (Task 14 implements it):
      - ``ctx_holder.current`` is the live ActionContext for the step being
        resolved, re-stamped to the current epoch after every settle.
      - ``ctx_holder.record(action)`` appends the action to the session's
        trace so later capture-slicing (spec 6.3) can find it.
      - ``async ctx_holder.refresh() -> Observation`` settles the page, takes
        a fresh epoch-stamped snapshot, updates ``ctx_holder.current`` to a
        baseline stamped with that new epoch, and updates the trace
        fingerprint used by capture-slicing. It is called after every action
        so the model is never shown a stale page (spec 5.1, 6.3).

    Ruling R2(b): a tool call must not return a bare acknowledgement. Spec 5.1
    requires the loop to "re-perceive and settle" between actions, and 6.3
    needs a pre-observation fingerprint recorded per action. Returning "ok"
    would freeze the model's view of the page at step entry and starve
    capture of the per-action fingerprints it slices on. So each tool result
    carries the freshly rendered post-action observation instead.
    """

    def _make(kind: str):
        @tool(kind, "Perform a " + kind + " on an enumerated element.",
              {"target_id": int, "value": str})
        async def handler(args):
            ctx = ctx_holder.current
            action = Action(
                kind=kind,
                target_id=int(args["target_id"]),
                value=args.get("value") or None,
                step_key=ctx.step.step_key,
                epoch=ctx.observation.epoch,
            )
            try:
                await execute(action, ctx, page, audit, vault, scrubber)
            except GuardRefusal as refusal:
                # Ruling R22(b). The refusal is recorded, which is what CLAUDE.md
                # already tells the session happens, and the reason is handed back
                # as the tool result so the model can satisfy it instead of
                # retrying the same action. An unhandled exception here would
                # surface to the session as a tool error with no stated reason,
                # which is the one thing the guard's carefully worded refusals
                # exist to avoid.
                audit.action_refused(action.step_key, kind=kind,
                                     target=_target_label(ctx, action),
                                     reason=refusal.reason,
                                     source=getattr(ctx, "source", "cold"))
                return {"content": [{"type": "text",
                                     "text": "Refused: " + refusal.reason}],
                        "is_error": True}
            ctx_holder.record(action)
            new_obs = await ctx_holder.refresh()
            return {"content": [{"type": "text",
                                  "text": render_observation(new_obs, scrubber)}]}

        return handler

    return [_make(k) for k in READ_TOOLS + WRITE_TOOLS]


def build_action_server(ctx_holder, page, audit, vault, scrubber):
    """Every tool routes to the one choke point. There is no second path."""
    return create_sdk_mcp_server(
        name=SERVER_NAME, version="1.0.0",
        tools=action_tools(ctx_holder, page, audit, vault, scrubber))
