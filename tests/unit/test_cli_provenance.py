"""What the run_started record can pin about a run. Spec 7.1, 8.1.

The audit is the only evidence a run leaves. A provenance field that silently
degrades to a placeholder is worse than an absent one, because a reader cannot
tell the difference between "not recorded" and "recorded as unknown".
"""
from importlib.metadata import version

from vba.cli import _prompt_hash, _sdk_version
from vba.resolve.prompts import SYSTEM


def test_the_sdk_version_is_really_discovered():
    """The model id these runs record is "default", meaning the CLI behind the SDK
    chose it. The SDK version is then the only thing narrowing what that resolved
    to, so it has to be the real one: an attribute rename that quietly turned this
    into "unknown" would leave the audit with no pin at all and say nothing."""
    got = _sdk_version()
    assert got != "unknown"
    assert got == version("claude-agent-sdk")


def test_the_prompt_hash_tracks_the_system_prompt():
    """Spec 7.1 records the prompt with the model, because a run reproduced against
    a different system prompt is not the same run. The hash must therefore be OF
    the prompt, not a constant that happens to look like one."""
    import hashlib

    assert _prompt_hash() == hashlib.sha256(
        SYSTEM.encode("utf-8")).hexdigest()[:12]
    assert len(_prompt_hash()) == 12
