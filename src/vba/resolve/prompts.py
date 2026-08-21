from vba.guard.scrub import Scrubber
from vba.perceive.elements import Observation

SYSTEM = """You resolve one step of an authored workflow against a live page.

You are given a numbered list of elements. Choose elements by their number.
You never write a CSS selector, an XPath, or a coordinate: those are not available
to you, and the tools will not accept them.

Rules:
- Do the current step's intent and nothing else. Do not proceed to later steps.
- To fill a credential field, pass the reference you were given (for example
  "portal:password") as the value. You will never be shown a secret, and you do not
  need one.
- If an approach is listed as known to fail, do not repeat it.
- When the step's intent is achieved, stop calling tools and say what you did.
"""


def render_observation(obs: Observation, scrubber: Scrubber) -> str:
    lines = ["URL: " + obs.url, "", "Elements:"]
    for e in obs.elements:
        bits = [str(e.target_id) + ".", e.role, repr(e.name)]
        if e.element_id:
            bits.append("id=" + e.element_id)
        if e.is_submit:
            bits.append("[submits the form]")
        lines.append("  " + " ".join(bits))
    return scrubber.clean("\n".join(lines))


def render_task(step, negatives, failure_context: str | None) -> str:
    parts = ["Step: " + step.step_key, "Intent: " + step.intent]
    if failure_context:
        parts += ["", "The previous attempt failed: " + failure_context]
    if negatives:
        parts += ["", "Approaches already known to fail for this step:"]
        parts += ["  - " + (n.failure_mode or "unspecified") for n in negatives]
    return "\n".join(parts)
