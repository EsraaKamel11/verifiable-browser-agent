from vba.act.actions import Action
from vba.contract.schema import Pii
from vba.guard.credentials import CredentialVault, should_screenshot
from vba.guard.scrub import Scrubber


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
