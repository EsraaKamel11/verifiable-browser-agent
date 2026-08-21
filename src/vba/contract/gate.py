from dataclasses import dataclass, field

from .schema import Contract


@dataclass(frozen=True)
class Grant:
    max_tier: int
    reason: str
    propose_only_tiers: set[int] = field(default_factory=set)


def evaluate_gate(contract: Contract) -> Grant:
    """Spec 4.2. Runs before any browser opens. Refusal is a first-class outcome."""
    o = contract.oracle
    if o is None or o.strength == "none":
        return Grant(
            max_tier=1,
            reason=(
                "No oracle binding. Tier 1 only: I can perceive the site and report "
                "what I see. To act I need a source of truth that confirms the act "
                "posted."
            ),
        )
    if o.strength == "on_page":
        return Grant(
            max_tier=2,
            reason="On-page oracle only. Irreversible acts are propose-only.",
            propose_only_tiers={3},
        )
    return Grant(max_tier=3, reason="Cross-system oracle bound. Full autonomy granted.")
