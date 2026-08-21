from vba.act.actions import Action, ActionContext
from vba.contract.gate import Grant
from vba.contract.schema import Pii, Step
from vba.guard.credentials import CredentialVault, resolve_fill_value, should_screenshot
from vba.guard.scrub import Scrubber
from vba.perceive.elements import Observation


def test_a_recorded_literal_is_removed_from_an_outbound_payload():
    """Spec 4.4: screenshot suppression alone is insufficient, because the loop
    re-perceives between actions and a post-fill observation is guaranteed."""
    s = Scrubber()
    s.record("Staging2026!")
    s.record("246810")
    cleaned = s.clean("code field shows 246810 and the password is Staging2026!")
    assert "246810" not in cleaned
    assert "Staging2026!" not in cleaned
    assert "[redacted]" in cleaned


def test_scrubbing_respects_token_boundaries():
    """Spec 4.4: a confirmation number that embeds a code's digits must survive.
    The world mints confirmations as PC-nnnnnn and the OTP is six digits."""
    s = Scrubber()
    s.record("246810")
    cleaned = s.clean("Confirmation number: PC-246810")
    assert "PC-246810" in cleaned


def test_the_vault_returns_a_secret_for_a_field_reference():
    v = CredentialVault({"portal:password": "Staging2026!"})
    assert v.get("portal", "password") == "Staging2026!"


def test_auth_urls_are_never_screenshotted():
    pii = Pii(redact=["password", "otp"], never_screenshot_urls=["/", "/login", "/verify"])
    assert should_screenshot("http://h/provider/1700000001", pii) is True
    assert should_screenshot("http://h/verify", pii) is False
    assert should_screenshot("http://h/login", pii) is False


def test_a_secret_never_appears_in_the_action_the_model_emitted():
    """The structural property: the model emits a reference, never a value."""
    a = Action(kind="fill", target_id=3, value="portal:password",
               step_key="portal.login", epoch=1)
    assert "Staging2026!" not in str(a)


def test_an_ordinary_colon_containing_literal_passes_through_unchanged():
    """Regression: credential references match only the full pattern \\w+:\\w+.
    Ordinary values like 'Room: 204' or '14:30' must pass through unchanged,
    not raise PermissionError."""
    step = Step(step_key="book.date", intent="enter date and room", tier=1)
    obs = Observation(url="http://h", epoch=7, elements=[], text="", fingerprint="f")
    ctx = ActionContext(
        step=step,
        grant=Grant(max_tier=1, reason="ok"),
        observation=obs,
        baseline=None
    )
    vault = CredentialVault({})
    scrubber = Scrubber()

    # These are ordinary literals, not credential references
    action_time = Action(kind="fill", target_id=0, value="14:30",
                        step_key="book.date", epoch=7)
    action_room = Action(kind="fill", target_id=0, value="Room: 204",
                        step_key="book.date", epoch=7)

    # Should return unchanged and raise nothing
    assert resolve_fill_value(action_time, ctx, vault, scrubber) == "14:30"
    assert resolve_fill_value(action_room, ctx, vault, scrubber) == "Room: 204"
