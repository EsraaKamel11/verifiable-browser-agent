"""The entry point run_demo.py shells out to. Spec 9.

Writes each run into runs/<run_id>/ so repeated demos do not append to one audit
chain and the report generator does not have to filter.
"""
import argparse
import asyncio
import hashlib
import os
import subprocess
import sys
import uuid
from pathlib import Path

from playwright.async_api import async_playwright

from vba.audit.log import AuditLog
from vba.contract.gate import evaluate_gate
from vba.contract.loader import load_contract
from vba.guard.credentials import CredentialVault
from vba.guard.scrub import Scrubber
from vba.memory.store import FixStore
from vba.oracle.client import OracleClient
from vba.report.render import render_report
from vba.resolve.prompts import SYSTEM
from vba.run.deps import Deps
from vba.run.drive import CtxHolder
from vba.run.machine import run_entity

BASE = os.environ.get("PORTAL_BASE", "http://127.0.0.1:8799")
ORACLE_BASE = os.environ.get("ORACLE_BASE", BASE)

CREDENTIAL_ENV = {
    "portal:email": "PORTAL_EMAIL",
    "portal:password": "PORTAL_PASSWORD",
    "portal:otp": "PORTAL_OTP",
}


def _commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def _prompt_hash() -> str:
    """Spec 7.1 records the prompt with the model and the commit, because a run
    reproduced against a different system prompt is not the same run."""
    return hashlib.sha256(SYSTEM.encode("utf-8")).hexdigest()[:12]


def _sdk_version() -> str:
    """The version of the client that resolved the model.

    VBA_MODEL names the model when it is set, and run_resolution pins the session
    to it. When it is not set, "default" is the honest answer: the CLI behind the
    SDK chooses, and this process never learns which id it chose. Recording a
    guess would be worse than recording none, so the SDK version is recorded
    instead as the thing that actually narrows what "default" resolved to.
    """
    try:
        import claude_agent_sdk
        return str(getattr(claude_agent_sdk, "__version__", "unknown"))
    except Exception:
        return "unknown"


def _vault() -> CredentialVault:
    values = {}
    missing = []
    for ref, env in CREDENTIAL_ENV.items():
        value = os.environ.get(env)
        if not value:
            missing.append(env)
        values[ref] = value or ""
    if missing:
        sys.exit("missing credentials in the environment: " + ", ".join(missing)
                 + ". tools/run_demo.py sets the world's staging values for you.")
    return CredentialVault(values)


async def main_async(args) -> int:
    contract = load_contract(args.contract)
    grant = evaluate_gate(contract)
    if grant.max_tier < 3:
        # Refusal is a first-class outcome, and a non-zero exit so a driver or a
        # CI step cannot read it as a completed run (spec 4.2).
        print("REFUSED. " + grant.reason)
        return 1

    run_dir = Path(args.runs_dir) / uuid.uuid4().hex[:8]
    run_dir.mkdir(parents=True, exist_ok=True)
    scrubber = Scrubber()
    audit = AuditLog(run_dir / "audit.jsonl", run_id=run_dir.name, scrubber=scrubber)
    audit.run_started({"model": os.environ.get("VBA_MODEL", "default"),
                       "sdk_version": _sdk_version(),
                       "commit": _commit(), "prompt_hash": _prompt_hash(),
                       "memory": args.memory, "contract": contract.name,
                       "version": contract.version, "payer": args.payer,
                       "providers": list(args.providers),
                       "chaos": os.environ.get("VBA_CHAOS", "")})

    vault = _vault()
    store = FixStore(Path(args.runs_dir) / "memory.db")
    oracle = OracleClient(ORACLE_BASE, contract.oracle.url)

    # Ruling R14, spec 4.2: "oracle declared but unreachable at start: refuse to
    # start". One read, before a browser exists, because an agent that cannot
    # confirm anything must not begin acting. Reachability is read off the reading
    # itself; an empty table would be indistinguishable from a dead endpoint.
    probe = await oracle.read(args.providers[0])
    if not probe.reachable:
        reason = ("Oracle declared but unreachable at start: refuse to start. "
                  "The record store at " + ORACLE_BASE + " did not answer a read "
                  "for " + args.providers[0] + ".")
        print("REFUSED. " + reason)
        audit.escalation("intake", "unverifiable", reason)
        return 1

    results = []
    async with async_playwright() as pw:
        # Demo tooling, not agent behaviour: VBA_HEADFUL=1 shows the browser and
        # VBA_SLOWMO paces it so a person can follow what the resolver is doing.
        # Neither changes what the agent perceives, decides, or records.
        browser = await pw.chromium.launch(
            headless=os.environ.get("VBA_HEADFUL", "") != "1",
            slow_mo=int(os.environ.get("VBA_SLOWMO", "0")),
        )
        for npi in args.providers:
            # One browser context per provider. Sessions and cookies do not leak
            # between entities, and the provider-level concurrency exhibit in spec
            # 3.4 becomes a change to this loop rather than a restructuring.
            #
            # VBA_VIDEO is demo tooling in the VBA_HEADFUL sense: it names a
            # directory and Playwright records the context's video there.
            # Recording observes the page the agent already renders; it changes
            # nothing the agent perceives, decides, or records, and the capture
            # PII policy does not apply because the video shows exactly what a
            # shoulder-surfer at the demo would see anyway.
            ctx_kwargs = {}
            video_dir = os.environ.get("VBA_VIDEO", "")
            if video_dir:
                ctx_kwargs["record_video_dir"] = video_dir
                ctx_kwargs["record_video_size"] = {"width": 1280, "height": 800}
                ctx_kwargs["viewport"] = {"width": 1280, "height": 800}
            page = await (await browser.new_context(**ctx_kwargs)).new_page()
            await page.goto(BASE + "/")
            deps = Deps(page=page, pii=contract.pii, audit=audit, vault=vault, scrubber=scrubber,
                        store=store, oracle=oracle, ctx_holder=CtxHolder(),
                        grant=grant, contract_name=contract.name,
                        memory_enabled=args.memory,
                        memory_writes_enabled=args.memory)
            # Ruling R13, once per page and before anything navigates: the field it
            # writes is what tells page_verify a 5xx apart from a missing control.
            deps.attach_response_listener()
            results.append(await run_entity(contract,
                                            {"npi": npi, "payer": args.payer},
                                            deps))
            # Demo tooling in the VBA_HEADFUL sense: a recording tail, so the
            # page the entity ended on is visibly in the context's video
            # before the next context starts or the browser closes. Off by
            # default; changes nothing the agent perceives, decides, records.
            tail_s = float(os.environ.get("VBA_DEMO_TAIL", "0") or 0)
            if tail_s:
                await page.wait_for_timeout(int(tail_s * 1000))
            if deps.halt_run:
                break
        await browser.close()

    (run_dir / "report.md").write_text(
        render_report(results, audit.records()), encoding="utf-8")
    print("run written to " + str(run_dir))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--providers", nargs="+", required=True)
    # Ruling R5. The contract's identity key is [npi, payer] and the contract
    # carries no payer VALUE, so the payer is an invocation parameter like the
    # identifier. It is also what makes a record filed under the page's default
    # payer MISFILED rather than confirmed.
    ap.add_argument("--payer", default="Aetna")
    ap.add_argument("--runs-dir", dest="runs_dir", default="runs")
    ap.add_argument("--no-memory", dest="memory", action="store_false")
    raise SystemExit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
