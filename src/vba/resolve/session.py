from claude_agent_sdk import ClaudeAgentOptions, query

from vba.act.server import SERVER_NAME, allowed_tools_for, build_action_server

from .prompts import SYSTEM, render_observation, render_task

MAX_TURNS = 12          # bounded autonomy: a resolution that cannot converge escalates
MAX_BUDGET_USD = 0.50


async def run_resolution(step, obs, ctx, negatives, deps, failure_context=None):
    """Spec 3.2. A top-level harness-spawned session, NOT an SDK subagent.

    The session acts through the granted tools; each call crosses the choke point.
    Nothing is returned as a plan: the actions have already happened, one at a time,
    and drive() collected them.

    SDK fields verified against the installed claude_agent_sdk==0.2.139
    (.venv/Lib/site-packages/claude_agent_sdk/types.py): permission_mode="dontAsk"
    is a real PermissionMode; setting_sources=["project"] is required to load
    CLAUDE.md ("Must include 'project' to load CLAUDE.md files" per its
    docstring); max_turns and max_budget_usd are real bounded-autonomy knobs;
    effort="medium" is a real EffortLevel. No brief kwarg was renamed or
    omitted here.
    """
    options = ClaudeAgentOptions(
        mcp_servers={SERVER_NAME: build_action_server(
            deps.ctx_holder, deps.page, deps.audit, deps.vault, deps.scrubber)},
        allowed_tools=allowed_tools_for(step, ctx.grant),
        permission_mode="dontAsk",
        system_prompt=SYSTEM,
        setting_sources=["project"],     # CLAUDE.md survives compaction
        max_turns=MAX_TURNS,
        max_budget_usd=MAX_BUDGET_USD,
        effort="medium",
    )
    prompt = "\n\n".join([
        render_task(step, negatives, failure_context),
        render_observation(obs, deps.scrubber),
    ])
    async for message in query(prompt=prompt, options=options):
        deps.audit.session_message(message)
