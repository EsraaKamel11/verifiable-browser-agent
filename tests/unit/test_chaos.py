"""The chaos hook. Ruling R24.

It is evaluation tooling living in the run layer, which is exactly the kind of code
that becomes a production incident when it fires by accident. These tests pin that
it cannot.
"""
import pytest

from vba.run import chaos


class Exploding:
    """Any network call at all is a failure of the inert property."""

    def __init__(self, *a, **kw):
        raise AssertionError("the chaos hook made a request when it should be inert")


async def test_the_hook_is_inert_with_no_environment_variable(monkeypatch):
    monkeypatch.delenv("VBA_CHAOS", raising=False)
    monkeypatch.setattr(chaos.httpx, "AsyncClient", Exploding)
    await chaos.fire(chaos.PORTAL_DOWN_BEFORE, "enrollment.submit")


async def test_a_directive_naming_another_step_does_not_fire(monkeypatch):
    monkeypatch.setenv("VBA_CHAOS", "portal_down_before:enrollment.submit")
    monkeypatch.setattr(chaos.httpx, "AsyncClient", Exploding)
    await chaos.fire(chaos.PORTAL_DOWN_BEFORE, "portal.login")


async def test_a_directive_for_the_other_hook_does_not_fire(monkeypatch):
    """Both directives in the rubric name the same step. Only the hook point
    distinguishes them, and firing the wrong one turns the outage case into the
    blackhole case without saying so."""
    monkeypatch.setenv("VBA_CHAOS", "blackhole_after_baseline:enrollment.submit")
    monkeypatch.setattr(chaos.httpx, "AsyncClient", Exploding)
    await chaos.fire(chaos.PORTAL_DOWN_BEFORE, "enrollment.submit")


def test_several_directives_are_parsed_from_one_variable(monkeypatch):
    monkeypatch.setenv("VBA_CHAOS", "portal_down_before:a.b , "
                                    "blackhole_after_baseline:c.d")
    assert chaos.directives() == [("portal_down_before", "a.b"),
                                  ("blackhole_after_baseline", "c.d")]


def test_the_portal_directive_targets_the_worlds_admin_endpoint(monkeypatch):
    monkeypatch.setenv("PORTAL_BASE", "http://127.0.0.1:8799/")
    assert chaos.endpoint_for(chaos.PORTAL_DOWN_BEFORE) == \
        "http://127.0.0.1:8799/admin/portal/down"


def test_the_blackhole_directive_targets_the_proxy_not_the_world(monkeypatch):
    """Spec 7.3: the world has no control that makes the record store unreachable,
    so this one must reach the proxy standing in front of it."""
    monkeypatch.setenv("PORTAL_BASE", "http://127.0.0.1:8799")
    monkeypatch.setenv("ORACLE_BASE", "http://127.0.0.1:8800")
    assert chaos.endpoint_for(chaos.BLACKHOLE_AFTER_BASELINE) == \
        "http://127.0.0.1:8800/control/blackhole/on"


def test_an_unknown_directive_is_a_loud_error(monkeypatch):
    """A typo in an eval's environment must not read as "no chaos requested"."""
    with pytest.raises(ValueError, match="unknown"):
        chaos.endpoint_for("portal_down_after_lunch")
