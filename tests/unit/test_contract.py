import pytest

from vba.contract.gate import evaluate_gate
from vba.contract.loader import load_contract
from vba.contract.schema import Contract, ContractError


def test_loads_the_shipped_contract():
    c = load_contract("contracts/payer_enrollment.yaml")
    assert c.name == "payer_enrollment"
    assert c.oracle.strength == "cross_system"
    assert [s.step_key for s in c.steps][-1] == "enrollment.submit"


def test_a_tier_3_step_must_declare_satisfied_when():
    """Spec 5.1: the tier-3 predicate's baseline requirement cannot be satisfied
    by a step that never reads one, so the schema enforces the coupling."""
    with pytest.raises(ContractError, match="satisfied_when"):
        Contract.model_validate({
            "contract": "x", "version": 1, "site": "s", "goal": "g",
            "oracle": {"kind": "http_json", "url": "u", "strength": "cross_system"},
            "identity": {"key": ["npi"], "resolve_ambiguity_by": "oracle"},
            "steps": [{"step_key": "a.b", "intent": "i", "tier": 3}],
            "pii": {"redact": [], "never_screenshot_urls": []},
        })


def test_cross_system_oracle_grants_tier_3():
    grant = evaluate_gate(load_contract("contracts/payer_enrollment.yaml"))
    assert grant.max_tier == 3


def test_no_oracle_refuses_tier_2_and_3():
    """Spec 4.2: refusal at intake is the generalization of cannot-confirm."""
    c = load_contract("contracts/payer_enrollment.yaml")
    c = c.model_copy(update={"oracle": None})
    grant = evaluate_gate(c)
    assert grant.max_tier == 1
    assert "oracle" in grant.reason.lower()


def test_on_page_oracle_makes_tier_3_propose_only():
    c = load_contract("contracts/payer_enrollment.yaml")
    c = c.model_copy(update={"oracle": c.oracle.model_copy(update={"strength": "on_page"})})
    grant = evaluate_gate(c)
    assert grant.max_tier == 2
    assert 3 in grant.propose_only_tiers
