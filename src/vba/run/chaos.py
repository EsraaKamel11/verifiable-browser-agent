"""Evaluation tooling. Ruling R24.

Two of the rubric cases (spec 7.2's portal outage, spec 7.3's unreachable record
store) need the world to change UNDER a run, at a named point inside a named step.
Prose cannot place an intervention there and a fixture cannot either, because the
whole point of both cases is that the change lands after the agent has already
committed to the step.

So the intervention is a hook in the harness, driven entirely by an environment
variable and inert without it. It has exactly two firing points, both in run_step,
and it never decides anything: it posts to an admin endpoint the evaluator could
have posted to by hand at the right millisecond.

    VBA_CHAOS="portal_down_before:enrollment.submit"
    VBA_CHAOS="blackhole_after_baseline:enrollment.submit"

Comma-separate to arm more than one. A directive naming a step other than the one
being run does nothing. A failed injection RAISES: a chaos case whose intervention
silently did not happen would report a pass that means nothing.
"""
import os

import httpx

PORTAL_DOWN_BEFORE = "portal_down_before"
BLACKHOLE_AFTER_BASELINE = "blackhole_after_baseline"

DEFAULT_BASE = "http://127.0.0.1:8799"


def _portal_base() -> str:
    return os.environ.get("PORTAL_BASE", DEFAULT_BASE).rstrip("/")


def _oracle_base() -> str:
    """The oracle's base is the blackhole proxy when one is in front of it, which
    is exactly when this directive is armed."""
    return os.environ.get("ORACLE_BASE", _portal_base()).rstrip("/")


_ENDPOINTS = {
    PORTAL_DOWN_BEFORE: lambda: _portal_base() + "/admin/portal/down",
    BLACKHOLE_AFTER_BASELINE: lambda: _oracle_base() + "/control/blackhole/on",
}


def directives() -> list[tuple[str, str]]:
    """Parsed (hook, step_key) pairs. Empty unless VBA_CHAOS is set."""
    out = []
    for raw in os.environ.get("VBA_CHAOS", "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        hook, _, step_key = raw.partition(":")
        out.append((hook.strip(), step_key.strip()))
    return out


def endpoint_for(hook: str) -> str:
    if hook not in _ENDPOINTS:
        raise ValueError("unknown VBA_CHAOS directive: " + hook)
    return _ENDPOINTS[hook]()


async def fire(hook: str, step_key: str) -> None:
    for armed_hook, armed_step in directives():
        if armed_hook != hook or armed_step != step_key:
            continue
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(endpoint_for(armed_hook))
            response.raise_for_status()
