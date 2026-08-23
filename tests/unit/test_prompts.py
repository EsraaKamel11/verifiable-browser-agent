"""What a resolution session is actually told.

The session is the only component that cannot be corrected after the fact: it acts
on the page. Everything it is not told, it guesses.
"""
from vba.contract.schema import CredentialRef, Step
from vba.guard.scrub import Scrubber
from vba.perceive.elements import Observation, elements_from_records
from vba.resolve.prompts import render_observation, render_task


NEGATIVES: list = []

BOUNCE_ELEMENTS = elements_from_records([
    {"tag": "a", "role": "link", "name": "Back", "element_id": "",
     "name_attr": "", "input_type": "", "is_submit": False, "selector": "a"},
])

# What the world serves when a required review checkbox was not ticked. Its only
# control is a Back link; the reason it bounced exists solely as prose.
BOUNCE = Observation(
    url="http://h/provider/1700000001/enroll",
    epoch=9,
    elements=BOUNCE_ELEMENTS,
    text="Please confirm you have reviewed this enrollment before submitting.\n\nBack",
    fingerprint="fp-bounce",
)


def test_the_rendered_observation_carries_the_page_text():
    """The defect this test was written for.

    On a page whose meaning is prose rather than controls, an element list alone
    tells the model nothing. A live self-heal failed here: the resolver was shown
    one line, "0. link 'Back'", and could not read the sentence naming what it had
    to do. Perception captured the text and then dropped it before the model.
    """
    rendered = render_observation(BOUNCE, Scrubber())
    assert "Please confirm you have reviewed this enrollment" in rendered


def test_the_element_list_survives_alongside_the_text():
    """The control: text must not displace the enumerated set, which is the only
    way the model can address anything."""
    rendered = render_observation(BOUNCE, Scrubber())
    assert "0." in rendered
    assert "'Back'" in rendered


def test_the_page_text_is_scrubbed():
    """Spec 4.4: the observation is an outbound payload, and the world's
    authenticator field is not a password input, so an unmasked code reaches the
    page text."""
    leaky = Observation(url="http://h/verify", epoch=1, elements=[],
                        text="Authenticator code 246810 accepted", fingerprint="f")
    scrubber = Scrubber()
    scrubber.record("246810")
    assert "246810" not in render_observation(leaky, scrubber)


def test_the_page_text_is_bounded():
    """A long page must not crowd out the element list or the context window."""
    long_page = Observation(url="http://h/x", epoch=1, elements=BOUNCE_ELEMENTS,
                            text="lorem ipsum " * 5000, fingerprint="f")
    rendered = render_observation(long_page, Scrubber())
    assert len(rendered) < 4000
    assert "'Back'" in rendered


def test_the_intent_is_bound_to_this_runs_parameters():
    """The shipped contract's intent for the record step reads "open the record for
    provider {npi}". Handed over raw, the placeholder is all the session sees, and
    a dashboard listing six providers offers six equally plausible links."""
    step = Step(step_key="provider.open",
                intent="open the record for provider {npi}", tier=1)
    text = render_task(step, NEGATIVES, None, {"npi": "1700000001"})
    assert "1700000001" in text
    assert "{npi}" not in text


def test_the_parameters_are_listed_even_when_the_intent_names_none():
    """The payer-selection intent is "select the payer named in the contract" and
    the contract carries no payer value: it arrives as a binding. Without the
    parameter block there is nothing in the prompt that names the payer at all,
    and a wrong selection files a real record under the wrong identity."""
    step = Step(step_key="enrollment.select_payer",
                intent="select the payer named in the contract", tier=2)
    text = render_task(step, NEGATIVES, None, {"npi": "1700000001",
                                               "payer": "Aetna"})
    assert "payer = Aetna" in text


def test_the_declared_credential_references_are_named_but_never_a_value():
    """Spec 4.4: the model passes a reference. It cannot pass the right one if it
    has to guess the field names, and a wrong guess is refused by the vault rather
    than corrected."""
    step = Step(step_key="portal.login", intent="sign in", tier=2,
                credentials=CredentialRef(ref="portal",
                                          fields=["email", "password"]))
    text = render_task(step, NEGATIVES, None, {"npi": "1700000001"})
    assert "portal:email" in text
    assert "portal:password" in text


def test_a_step_with_no_credentials_is_offered_none():
    step = Step(step_key="provider.open", intent="open the record", tier=1)
    text = render_task(step, NEGATIVES, None, {"npi": "1700000001"})
    assert "portal:" not in text


def test_the_task_text_survives_scrubbing_with_no_secret_in_it():
    """The task block is scrubbed with the observation (spec 4.4). Bindings and
    references are not secrets, so nothing in it should be redacted away."""
    scrubber = Scrubber()
    scrubber.record("Staging2026!")
    step = Step(step_key="portal.login", intent="sign in", tier=2,
                credentials=CredentialRef(ref="portal", fields=["password"]))
    text = scrubber.clean(render_task(step, NEGATIVES, None, {"npi": "1700000001"}))
    assert "portal:password" in text
    assert "Staging2026!" not in text
