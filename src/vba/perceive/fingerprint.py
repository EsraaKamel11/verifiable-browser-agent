import hashlib
import json
import re
from urllib.parse import urlsplit

from .elements import Element

# Path segments that are identifiers get templated. Extend deliberately, not casually:
# an over-eager rule collapses genuinely different pages into one fingerprint.
_ID_SEGMENT = re.compile(r"^\d{6,}$")


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    segs = ["{id}" if _ID_SEGMENT.match(s) else s for s in parts.path.split("/")]
    return parts.netloc + "/".join(segs)


def form_signature(elements: list[Element]) -> str:
    """Attribute-level, not accessible names.

    Named controls contribute (tag, name attribute, type), which are value-free and
    therefore identical across entities. Buttons contribute (id, accessible name),
    which is what discriminates layouts whose named inputs are identical.
    """
    named = sorted(
        (e.tag, e.name_attr, e.input_type) for e in elements if e.name_attr
    )
    buttons = sorted(
        (e.element_id, e.name) for e in elements if e.is_submit or e.role == "button"
    )
    return json.dumps({"named": named, "buttons": buttons}, sort_keys=True)


def control_set(elements: list[Element]) -> str:
    """Presence, not selection state. Which option is currently chosen is state."""
    return json.dumps(sorted({(e.tag, e.role) for e in elements}), sort_keys=True)


def fingerprint(contract: str, step_key: str, url: str, elements: list[Element]) -> str:
    payload = "|".join([
        contract, step_key, normalize_url(url),
        form_signature(elements), control_set(elements),
    ])
    return hashlib.sha256(payload.encode()).hexdigest()
