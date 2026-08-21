from dataclasses import dataclass, field

from vba.oracle.delta import Outcome, PageVerdict


@dataclass
class StepOutcome:
    step_key: str
    outcome: Outcome
    page: PageVerdict
    source: str                 # "memory:<fix_id>" or "cold"
    verif_strength: str
    detail: str = ""


@dataclass
class RunResult:
    entity: dict
    outcomes: list[StepOutcome] = field(default_factory=list)
    terminal: Outcome | None = None
    escalated: bool = False
    escalation_reason: str = ""
