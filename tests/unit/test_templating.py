# tests/unit/test_templating.py
from vba.memory.templating import bind, template


BINDINGS = {"npi": "1700000001", "payer": "Aetna"}


def test_a_parameter_embedded_in_surrounding_text_is_templated():
    """Spec 6.1: equality would store this whole, because the accessible name of a
    dashboard link is not EQUAL to any parameter, it CONTAINS one."""
    name = "1700000001 - Dr. Maria Santos (Family Medicine)"
    assert template(name, BINDINGS) == "{npi} - Dr. Maria Santos (Family Medicine)"


def test_a_bare_parameter_value_is_templated():
    assert template("Aetna", BINDINGS) == "{payer}"


def test_binding_restores_the_current_invocation_values():
    stored = "{npi} - Dr. Maria Santos (Family Medicine)"
    assert bind(stored, BINDINGS) == "1700000001 - Dr. Maria Santos (Family Medicine)"


def test_the_residual_literal_is_what_protects_against_a_wrong_entity_act():
    """Spec 6.1: bound for a DIFFERENT entity, the residual name matches nothing on
    the page, so the lookup misses and resolution runs cold. Failing safe."""
    stored = "{npi} - Dr. Maria Santos (Family Medicine)"
    other = bind(stored, {"npi": "1700000002", "payer": "Cigna"})
    assert other == "1700000002 - Dr. Maria Santos (Family Medicine)"
    assert other != "1700000002 - Dr. James Okafor (Cardiology)"


def test_longer_parameter_values_are_templated_first():
    """Otherwise a short value that is a substring of a longer one corrupts it."""
    b = {"a": "170", "b": "1700000001"}
    assert template("1700000001", b) == "{b}"


def test_text_containing_no_parameter_is_unchanged():
    assert template("Submit enrollment", BINDINGS) == "Submit enrollment"
