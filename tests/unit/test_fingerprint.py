from vba.perceive.elements import elements_from_records
from vba.perceive.fingerprint import fingerprint, normalize_url


def _rec(**kw):
    base = {"tag": "input", "role": "textbox", "name": "", "element_id": "",
            "name_attr": "", "input_type": "text", "is_submit": False, "selector": ""}
    base.update(kw)
    return base


# Two providers, one layout. Only interpolated content differs.
PROVIDER_A = [
    _rec(tag="input", element_id="npi", name_attr="npi", name="NPI 1700000001"),
    _rec(tag="select", role="combobox", element_id="payer", name_attr="payer", name="Payer"),
    _rec(tag="button", role="button", element_id="submit-enrollment",
         name="Submit enrollment", input_type="submit", is_submit=True),
]
PROVIDER_B = [
    _rec(tag="input", element_id="npi", name_attr="npi", name="NPI 1700000002"),
    _rec(tag="select", role="combobox", element_id="payer", name_attr="payer", name="Payer"),
    _rec(tag="button", role="button", element_id="submit-enrollment",
         name="Submit enrollment", input_type="submit", is_submit=True),
]
# Layout B: control renamed, and a required checkbox added.
LAYOUT_B = [
    _rec(tag="input", element_id="npi", name_attr="npi", name="NPI 1700000001"),
    _rec(tag="select", role="combobox", element_id="payer", name_attr="payer", name="Payer"),
    _rec(tag="input", role="checkbox", element_id="reviewed", name_attr="reviewed",
         input_type="checkbox", name="I have reviewed this enrollment"),
    _rec(tag="button", role="button", element_id="confirm-and-submit",
         name="Confirm and submit enrollment", input_type="submit", is_submit=True),
]
# Layout C: renamed again, no checkbox. Named inputs are IDENTICAL to layout A.
LAYOUT_C = [
    _rec(tag="input", element_id="npi", name_attr="npi", name="NPI 1700000001"),
    _rec(tag="select", role="combobox", element_id="payer", name_attr="payer", name="Payer"),
    _rec(tag="button", role="button", element_id="place-enrollment",
         name="Place enrollment", input_type="submit", is_submit=True),
]


def _fp(records, url="http://h/provider/1700000001"):
    return fingerprint("payer_enrollment", "enrollment.submit", url,
                       elements_from_records(records))


def test_url_path_parameters_are_templated_out():
    """Spec 6.2: without this, a fix learned on one provider never hits for another."""
    assert (normalize_url("http://h/provider/1700000001")
            == normalize_url("http://h/provider/1700000002"))


def test_the_same_layout_fingerprints_identically_across_providers():
    """The load-bearing invariance. If this fails, memory never reuses anything."""
    assert _fp(PROVIDER_A) == _fp(PROVIDER_B, "http://h/provider/1700000002")


def test_each_layout_fingerprints_differently():
    assert len({_fp(PROVIDER_A), _fp(LAYOUT_B), _fp(LAYOUT_C)}) == 3


def test_layouts_a_and_c_do_not_collide_despite_identical_named_inputs():
    """Spec 6.2: buttons must be included by id and text, or A and C collapse.
    Both layouts carry exactly npi and payer as named controls."""
    named_a = sorted(r["name_attr"] for r in PROVIDER_A if r["name_attr"])
    named_c = sorted(r["name_attr"] for r in LAYOUT_C if r["name_attr"])
    assert named_a == named_c            # the premise of the trap
    assert _fp(PROVIDER_A) != _fp(LAYOUT_C)


def test_accessible_names_of_fields_do_not_affect_the_fingerprint():
    """Spec 6.2 and the Task 1 finding: a name that absorbs a field value would
    reintroduce per-provider drift through the back door."""
    mutated = [dict(r) for r in PROVIDER_A]
    mutated[0]["name"] = "NPI 9999999999"
    assert _fp(PROVIDER_A) == _fp(mutated)
