import math

from vba.perceive.elements import Element

from .store import StoredAction
from .templating import template


def slice_capture(entry_fingerprint: str, trace: list[tuple[str, object]]) -> list[object]:
    """Spec 6.3.

    Capture the action suffix beginning at the START of the final contiguous run of
    observations whose fingerprint matches the step's entry state.

    Formally: find the latest observation that does NOT match entry, and capture from
    the first matching observation after it. The naive "from the last matching
    observation" drops any action that did not change the structural fingerprint,
    which is precisely the required checkbox tick.
    """
    last_mismatch = -1
    for i, (fp, _action) in enumerate(trace):
        if fp != entry_fingerprint:
            last_mismatch = i
    return [action for fp, action in trace[last_mismatch + 1:]]


def to_stored_actions(
    pairs: list[tuple[Element, str, str | None]], bindings: dict[str, str]
) -> list[StoredAction]:
    """Template every stored string against the capture invocation's bindings."""
    out = []
    for element, kind, value in pairs:
        out.append(StoredAction(
            kind=kind,
            identity_id=template(element.element_id, bindings),
            identity_role=element.role,
            identity_name=template(element.name, bindings),
            value=template(value, bindings) if value else None,
            is_submit=element.is_submit,
        ))
    return out


# Weights and the decay constant ship UNCALIBRATED and gate nothing (spec 6.4, 10.2).
W_VERIF, W_SUCCESS, W_RECENCY = 0.4, 0.4, 0.2
TAU_DAYS = 45.0


def confidence(verif_strength: str, successes: int, trials: int, age_days: float) -> float:
    v = {"cross_system": 1.0, "on_page": 0.5}.get(verif_strength, 0.0)
    s = (successes + 1) / (trials + 2)          # Laplace: one lucky run is not trust
    r = math.exp(-age_days / TAU_DAYS)          # staleness is re-verified more eagerly
    return max(0.0, min(1.0, W_VERIF * v + W_SUCCESS * s + W_RECENCY * r))
