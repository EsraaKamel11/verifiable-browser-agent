"""What a resolution session is actually told.

The session is the only component that cannot be corrected after the fact: it acts
on the page. Everything it is not told, it guesses.
"""
from vba.contract.schema import CredentialRef, Step
from vba.guard.scrub import Scrubber
from vba.resolve.prompts import render_task


NEGATIVES: list = []


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
