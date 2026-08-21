from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ContractError(Exception):
    pass


class Oracle(BaseModel):
    kind: Literal["http_json"]
    url: str
    strength: Literal["cross_system", "on_page", "none"]


class Identity(BaseModel):
    key: list[str]
    resolve_ambiguity_by: Literal["oracle"]


class CredentialRef(BaseModel):
    ref: str
    fields: list[str]


class Postcondition(BaseModel):
    text_present: str | None = None
    text_absent: str | None = None


class Step(BaseModel):
    step_key: str
    intent: str
    tier: int = Field(ge=1, le=3)
    # The explicit exemption spec 4.3 calls for. The shaping rule classifies any
    # submit-type control as tier 3, because firing a form is how an unbaselined
    # record gets posted. An authentication form posts no record, and the contract
    # is the only artifact that knows which forms do. Declaring it here keeps the
    # exemption per step and off by default: a step that does not declare it still
    # cannot fire any form. It is auditable in the literal sense that every
    # permitted action records both this flag and whether its target was a submit
    # control (see AuditLog.action_permitted), so a reader can tell an exempted
    # act from an ordinary tier-2 click without consulting the contract.
    fires_form: bool = False
    credentials: CredentialRef | None = None
    preconditions: list[str] = Field(default_factory=list)
    satisfied_when: str | None = None
    postconditions: list[Postcondition] = Field(default_factory=list)


class Pii(BaseModel):
    redact: list[str] = Field(default_factory=list)
    never_screenshot_urls: list[str] = Field(default_factory=list)


class Contract(BaseModel):
    name: str = Field(alias="contract")
    version: int
    site: str
    goal: str
    oracle: Oracle | None = None
    identity: Identity
    steps: list[Step]
    pii: Pii

    @model_validator(mode="after")
    def _tier3_requires_satisfied_when(self):
        for s in self.steps:
            if s.tier == 3 and not s.satisfied_when:
                raise ContractError(
                    "step " + repr(s.step_key) + " is tier 3 but declares no "
                    "satisfied_when; a tier-3 act must read a baseline it will check"
                )
        return self
