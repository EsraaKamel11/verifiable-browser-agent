from vba.guard.scrub import Scrubber
from vba.memory.templating import bind
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
- Finish the step you were given. If its work is typed into a form that only takes
  effect once the form is confirmed, use that form's own confirm control. If the
  guard refuses that control, then confirming is not part of this step: read the
  stated reason and stop rather than looking for another way to do it.
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


def render_task(step, negatives, failure_context: str | None,
                bindings: dict[str, str] | None = None) -> str:
    """The step, its parameters, and the credential references it may pass.

    The intent is templated against the invocation's bindings, and the bindings
    themselves are listed. A contract intent reads "open the record for provider
    {npi}" and "select the payer named in the contract"; a session handed those
    strings raw is being asked to guess which provider and which payer, and it
    will guess. The bindings are the entity being worked, so they belong in the
    task text rather than only in the harness.

    Credential REFERENCES are listed for the same reason: the step declares which
    ones it is authorized to fill, and a session that guesses the wrong field name
    trips the vault's authorization check instead of signing in. A reference is not
    a secret; no value ever appears here.
    """
    values = dict(bindings or {})
    parts = ["Step: " + step.step_key, "Intent: " + bind(step.intent, values)]
    if values:
        parts += ["", "Parameters for this run:"]
        parts += ["  " + k + " = " + str(v) for k, v in values.items()]
    creds = getattr(step, "credentials", None)
    if creds is not None:
        parts += ["", "Credential references you may pass as a fill value here "
                      "(never a literal value):"]
        parts += ["  " + creds.ref + ":" + f for f in creds.fields]
    if failure_context:
        parts += ["", "The previous attempt failed: " + failure_context]
    if negatives:
        parts += ["", "Approaches already known to fail for this step:"]
        parts += ["  - " + (n.failure_mode or "unspecified") for n in negatives]
    return "\n".join(parts)
