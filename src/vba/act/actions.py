from dataclasses import dataclass
from typing import Any, Literal

ActionKind = Literal[
    "click", "fill", "select", "hover", "scroll", "navigate", "submit", "extract", "draw"
]


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    target_id: int
    value: str | None
    step_key: str
    epoch: int


@dataclass(frozen=True)
class ActionContext:
    step: Any        # contract.schema.Step
    grant: Any       # contract.gate.Grant
    observation: Any # perceive.elements.Observation
    baseline: Any    # oracle.delta.Baseline | None
