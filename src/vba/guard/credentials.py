from vba.act.actions import Action, ActionContext
from vba.contract.schema import Pii

from .scrub import Scrubber


class CredentialVault:
    """Values come from the environment. The model never receives one."""

    def __init__(self, values: dict[str, str]):
        self._values = values

    def get(self, ref: str, field: str) -> str:
        key = ref + ":" + field
        if key not in self._values:
            raise KeyError("no credential for " + key)
        return self._values[key]


def resolve_fill_value(
    action: Action, ctx: ActionContext, vault: CredentialVault, scrubber: Scrubber
) -> str:
    """The model emitted a reference like "portal:password". Resolve it here, record
    the literal for scrubbing, and hand the value straight to the browser."""
    import re
    raw = action.value or ""
    # A credential reference must match the full pattern of identifier:identifier.
    # Each part must start with a letter or underscore. Ordinary values like "Room: 204"
    # or "14:30" pass through unchanged. Pattern: [a-zA-Z_]\w*:[a-zA-Z_]\w+
    if not re.fullmatch(r"[a-zA-Z_]\w*:[a-zA-Z_]\w*", raw):
        return raw                       # an ordinary value, not a credential
    ref, _, field = raw.partition(":")
    creds = ctx.step.credentials
    if creds is None or creds.ref != ref or field not in creds.fields:
        raise PermissionError(
            "step " + repr(ctx.step.step_key) + " is not authorized to fill "
            + repr(raw) + " under its contract"
        )
    secret = vault.get(ref, field)
    scrubber.record(secret)
    return secret


def should_screenshot(url: str, pii: Pii) -> bool:
    from urllib.parse import urlsplit
    path = urlsplit(url).path or "/"
    return path not in pii.never_screenshot_urls
