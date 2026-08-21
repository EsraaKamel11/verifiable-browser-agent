# tests/unit/test_elements.py
from vba.perceive.elements import Element, elements_from_records


RECORDS = [
    {"tag": "a", "role": "link", "name": "1700000001 - Dr. Maria Santos (Family Medicine)",
     "element_id": "", "name_attr": "", "input_type": "", "is_submit": False,
     "selector": "a[href='/provider/1700000001']"},
    {"tag": "button", "role": "button", "name": "Submit enrollment",
     "element_id": "submit-enrollment", "name_attr": "", "input_type": "submit",
     "is_submit": True, "selector": "#submit-enrollment"},
    {"tag": "input", "role": "textbox", "name": "NPI", "element_id": "npi",
     "name_attr": "npi", "input_type": "text", "is_submit": False, "selector": "#npi"},
]


def test_target_ids_are_dense_and_ordered():
    els = elements_from_records(RECORDS)
    assert [e.target_id for e in els] == [0, 1, 2]


def test_submit_controls_are_flagged_from_metadata():
    """Spec 4.3 shaping rule: the choke point classifies a submit from element
    metadata, not from what the resolver called the action."""
    els = elements_from_records(RECORDS)
    assert [e.is_submit for e in els] == [False, True, False]


def test_an_element_carries_its_identity_components():
    """Spec 6.4: still_resolves checks id, role and accessible name together."""
    submit = elements_from_records(RECORDS)[1]
    assert (submit.element_id, submit.role, submit.name) == (
        "submit-enrollment", "button", "Submit enrollment")
