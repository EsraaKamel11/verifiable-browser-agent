from claude_agent_sdk import create_sdk_mcp_server, tool

from vba.act.actions import Action
from vba.act.choke import execute
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


def build_action_server(ctx_holder, page, audit, vault, scrubber):
    """Every tool routes to the one choke point. There is no second path.

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
            await execute(action, ctx, page, audit, vault, scrubber)
            ctx_holder.record(action)
            new_obs = await ctx_holder.refresh()
            return {"content": [{"type": "text",
                                  "text": render_observation(new_obs, scrubber)}]}

        return handler

    tools = [_make(k) for k in READ_TOOLS + WRITE_TOOLS]
    return create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=tools)
