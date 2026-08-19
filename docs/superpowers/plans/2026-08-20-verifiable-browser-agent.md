# Verifiable Browser Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a browser agent that automates an authenticated enrollment workflow and proves whether each enrollment actually posted, against a system of record rather than the page that claims success.

**Architecture:** A deterministic Python harness owns the loop, both verification paths, memory, and the audit trail. A Claude Agent SDK session is spawned only when a step needs resolving (memory miss or a failure needing a new path); Playwright sits behind an in-process MCP server exposing an enumerated action space. Every action crosses one choke point where the tier guard runs, so memory-originated and model-originated actions get identical enforcement.

**Tech Stack:** Python 3.11+, `claude-agent-sdk`, `playwright`, `mcp`, `pydantic`, `pydantic-evals`, `httpx`, `pytest`, SQLite.

**Spec:** `docs/superpowers/specs/2026-08-17-verifiable-browser-agent-design.md` (commit `1dd3f29`). Read it before starting. Every task below cites the spec section it implements.

## Global Constraints

- **No LLM judges anything.** Verification, tier enforcement, retry adjudication, memory promotion, and pass/fail scoring are all deterministic predicates. (Spec 3.4, 7.5)
- **The oracle is never a tool.** The model cannot call, skip, or influence system verification. The harness runs it after every step with `satisfied_when`. (Spec 4.3)
- **One choke point.** `execute()` in `act/` is the only path to a side effect, and `guard.check()` runs inside it. Nothing bypasses it, including replayed memory actions. (Spec 3.1, 4.3)
- **Never a raw viewport coordinate.** Actions reference `target_id` into an enumerated set. (Spec 3.1)
- **Never retry an irreversible act on ambiguity.** Read the oracle first. (Spec 5.5)
- **Confidence gates nothing.** It is computed and reported only. (Spec 6.4)
- **Credentials never reach the model.** The model emits a field reference; the guard injects the value and scrubs injected literals from every outbound payload. (Spec 4.4)
- **No em-dashes or en-dashes in any prose file** (README, reports, docs). House rule for this document family.
- **No company or product names in committed code or docs.** The simulated world is "the target world"; use generic naming in the agent. (Spec 10)
- **Python 3.11+.** Pin exact versions in `pyproject.toml` at first install and do not float them.

---

## File Structure

```
src/vba/
  contract/   schema.py  loader.py  gate.py          Task 2
  perceive/   snapshot.py  elements.py  fingerprint.py  Tasks 3, 4
  act/        actions.py  choke.py  server.py        Tasks 5, 11
  guard/      tiers.py  credentials.py  scrub.py     Tasks 5, 6
  oracle/     client.py  delta.py                    Task 7
  verify/     page.py  system.py                     Tasks 7, 8
  memory/     store.py  templating.py  capture.py    Tasks 9, 10
  resolve/    session.py  prompts.py                 Task 11
  run/        machine.py  outcomes.py  escalate.py   Task 12
  audit/      log.py  chain.py                       Task 13
  report/     render.py  rederive.py                 Task 13
contracts/    payer_enrollment.yaml                  Task 2
world/        vendored simulation, run as subprocess  Task 1
tests/
  unit/       tier 1, keyless                        Tasks 2-13 inline + 14
  fixtures/   external pages, recorded trajectories  Tasks 14, 15
  world/      tier 2, world-backed deterministic     Task 15
  evals/      tier 3, live model                     Task 16
tools/        run_demo.py  blackhole_proxy.py        Tasks 16, 15
```

**Why these boundaries.** `verify/page.py` and `verify/system.py` are separate files because the spec forbids one deciding what the other decides. `guard/` holds three related enforcement duties that all fire at the same choke point. `run/` exists so the state machine is not an unnamed harness that grows without limit.

---

## Task 1: Repo skeleton, vendored world, and the empirical check that gates the fingerprint design

**Spec:** 11 (open question 1), 9. **This task must run first**, because its outcome can change Task 4.

**Files:**
- Create: `pyproject.toml`, `src/vba/__init__.py`, `tests/__init__.py`
- Create: `world/` (vendored copy of the target simulation)
- Create: `tools/probe_accname.py`
- Create: `docs/findings/2026-08-20-accname-probe.md`

**Interfaces:**
- Consumes: nothing.
- Produces: a runnable world at `http://127.0.0.1:8799`; a recorded finding stating whether accessible names absorb field values.

- [ ] **Step 1: Create `pyproject.toml` with pinned dependencies**

```toml
[project]
name = "vba"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "claude-agent-sdk==0.2.139",
    "playwright==1.49.0",
    "pydantic==2.12.5",
    "httpx==0.28.1",
    "pyyaml==6.0.2",
]

[project.optional-dependencies]
dev = ["pytest==8.4.2", "pytest-asyncio==0.24.0", "pydantic-evals==2.31.0"]
world = ["fastapi==0.115.6", "uvicorn==0.34.0"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "world: needs the target world running on 127.0.0.1:8799",
    "evals: needs a model API key and costs money",
]
```

Then run:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev,world]"
.venv/Scripts/python -m playwright install chromium
```

- [ ] **Step 2: Vendor the world**

Copy every `.py` file from `the seeding scenario's world files:` into `world/`. It uses flat imports (`import system_of_record`), so it runs as a subprocess from its own directory and is never imported by `src/vba`.

Write `world/README.md` stating: this is the target simulation, vendored so the evaluation is reproducible; it was authored by the same author as the agent, which spec section 10.1 addresses directly.

- [ ] **Step 3: Verify the world runs**

Run: `.venv/Scripts/python world/run_world.py`
Then in a second shell: `curl http://127.0.0.1:8799/healthz`
Expected: HTTP 200. Leave the world running for step 5.

- [ ] **Step 4: Write the accessible-name probe**

```python
# tools/probe_accname.py
"""Answer spec open question 1: do accessible names absorb field values?

The record page renders the NPI field as an input inside its own label. Under the
accname algorithm an embedded control inside its own label can contribute its VALUE
to its own name. If that happens here, the fingerprint must avoid accessible names
entirely (spec 6.2), because the name would differ per provider and one layout would
mint one fingerprint per provider.

Run this against a real snapshot. Do not reason about it.
"""
import asyncio
import json

from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8799"


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(URL + "/")
        await page.fill("#username", "ops@cascade-credentialing.example")
        await page.fill("#password", "Staging2026!")
        await page.click("#sign-in")
        await page.fill("#otp", "246810")
        await page.check("#not-a-robot")
        await page.click("#verify")
        await page.goto(URL + "/provider/1700000001")
        snapshot = await page.accessibility.snapshot()
        print(json.dumps(snapshot, indent=2))
        await browser.close()


asyncio.run(main())
```

- [ ] **Step 5: Run the probe and record the finding**

Run: `.venv/Scripts/python tools/probe_accname.py > probe_output.json`

Find the NPI field's node in the output. Write `docs/findings/2026-08-20-accname-probe.md` recording the node's `name` value verbatim, and stating one of:

- **Names absorb values** (the name contains `1700000001`): the attribute-level fingerprint in Task 4 is load-bearing. Proceed exactly as specced.
- **Names do not absorb values** (the name is `NPI` or similar): the constraint relaxes. Still build the attribute-level fingerprint, because it is more robust regardless, but record that its justification is prudence rather than necessity, and update the "must be validated" paragraph in spec section 6.2 with this finding.

Delete `probe_output.json`. The finding document is the artifact.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src tests world tools docs/findings
git commit -m "chore: skeleton, vendored world, accessible-name probe finding"
```

---

## Task 2: Contract schema, loader, and acceptance gate

**Spec:** 4.1, 4.2.

**Files:**
- Create: `src/vba/contract/__init__.py`, `src/vba/contract/schema.py`, `src/vba/contract/loader.py`, `src/vba/contract/gate.py`
- Create: `contracts/payer_enrollment.yaml`
- Test: `tests/unit/test_contract.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Step(step_key: str, intent: str, tier: int, credentials: CredentialRef | None, preconditions: list[str], satisfied_when: str | None, postconditions: list[Postcondition])`
  - `Oracle(kind: str, url: str, strength: Literal["cross_system","on_page","none"])`
  - `Contract(name: str, version: int, site: str, goal: str, oracle: Oracle | None, identity: Identity, steps: list[Step], pii: Pii)`
  - `load_contract(path: str) -> Contract`
  - `Grant(max_tier: int, reason: str, propose_only_tiers: set[int])`
  - `evaluate_gate(contract: Contract) -> Grant`
  - `class ContractError(Exception)`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_contract.py
import pytest

from vba.contract.gate import evaluate_gate
from vba.contract.loader import load_contract
from vba.contract.schema import Contract, ContractError


def test_loads_the_shipped_contract():
    c = load_contract("contracts/payer_enrollment.yaml")
    assert c.name == "payer_enrollment"
    assert c.oracle.strength == "cross_system"
    assert [s.step_key for s in c.steps][-1] == "enrollment.submit"


def test_a_tier_3_step_must_declare_satisfied_when():
    """Spec 5.1: the tier-3 predicate's baseline requirement cannot be satisfied
    by a step that never reads one, so the schema enforces the coupling."""
    with pytest.raises(ContractError, match="satisfied_when"):
        Contract.model_validate({
            "contract": "x", "version": 1, "site": "s", "goal": "g",
            "oracle": {"kind": "http_json", "url": "u", "strength": "cross_system"},
            "identity": {"key": ["npi"], "resolve_ambiguity_by": "oracle"},
            "steps": [{"step_key": "a.b", "intent": "i", "tier": 3}],
            "pii": {"redact": [], "never_screenshot_urls": []},
        })


def test_cross_system_oracle_grants_tier_3():
    grant = evaluate_gate(load_contract("contracts/payer_enrollment.yaml"))
    assert grant.max_tier == 3


def test_no_oracle_refuses_tier_2_and_3():
    """Spec 4.2: refusal at intake is the generalization of cannot-confirm."""
    c = load_contract("contracts/payer_enrollment.yaml")
    c = c.model_copy(update={"oracle": None})
    grant = evaluate_gate(c)
    assert grant.max_tier == 1
    assert "oracle" in grant.reason.lower()


def test_on_page_oracle_makes_tier_3_propose_only():
    c = load_contract("contracts/payer_enrollment.yaml")
    c = c.model_copy(update={"oracle": c.oracle.model_copy(update={"strength": "on_page"})})
    grant = evaluate_gate(c)
    assert grant.max_tier == 2
    assert 3 in grant.propose_only_tiers
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_contract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vba.contract'`

- [ ] **Step 3: Write `contracts/payer_enrollment.yaml`**

Copy the YAML block from spec section 4.1 verbatim into this file. It is the authored contract, not an illustration.

- [ ] **Step 4: Implement the schema**

```python
# src/vba/contract/schema.py
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ContractError(Exception):
    pass


class Oracle(BaseModel):
    kind: Literal["http_json"]
    url: str
    strength: Literal["cross_system", "on_page", "none"]


class Identity(BaseModel):
    key: list[str]
    resolve_ambiguity_by: Literal["oracle"]


class CredentialRef(BaseModel):
    ref: str
    fields: list[str]


class Postcondition(BaseModel):
    text_present: str | None = None
    text_absent: str | None = None


class Step(BaseModel):
    step_key: str
    intent: str
    tier: int = Field(ge=1, le=3)
    credentials: CredentialRef | None = None
    preconditions: list[str] = Field(default_factory=list)
    satisfied_when: str | None = None
    postconditions: list[Postcondition] = Field(default_factory=list)


class Pii(BaseModel):
    redact: list[str] = Field(default_factory=list)
    never_screenshot_urls: list[str] = Field(default_factory=list)


class Contract(BaseModel):
    name: str = Field(alias="contract")
    version: int
    site: str
    goal: str
    oracle: Oracle | None = None
    identity: Identity
    steps: list[Step]
    pii: Pii

    @model_validator(mode="after")
    def _tier3_requires_satisfied_when(self):
        for s in self.steps:
            if s.tier == 3 and not s.satisfied_when:
                raise ContractError(
                    "step " + repr(s.step_key) + " is tier 3 but declares no "
                    "satisfied_when; a tier-3 act must read a baseline it will check"
                )
        return self
```

- [ ] **Step 5: Implement the loader and the gate**

```python
# src/vba/contract/loader.py
import yaml

from .schema import Contract


def load_contract(path: str) -> Contract:
    with open(path, encoding="utf-8") as fh:
        return Contract.model_validate(yaml.safe_load(fh))
```

```python
# src/vba/contract/gate.py
from dataclasses import dataclass, field

from .schema import Contract


@dataclass(frozen=True)
class Grant:
    max_tier: int
    reason: str
    propose_only_tiers: set[int] = field(default_factory=set)


def evaluate_gate(contract: Contract) -> Grant:
    """Spec 4.2. Runs before any browser opens. Refusal is a first-class outcome."""
    o = contract.oracle
    if o is None or o.strength == "none":
        return Grant(
            max_tier=1,
            reason=(
                "No oracle binding. Tier 1 only: I can perceive the site and report "
                "what I see. To act I need a source of truth that confirms the act "
                "posted."
            ),
        )
    if o.strength == "on_page":
        return Grant(
            max_tier=2,
            reason="On-page oracle only. Irreversible acts are propose-only.",
            propose_only_tiers={3},
        )
    return Grant(max_tier=3, reason="Cross-system oracle bound. Full autonomy granted.")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_contract.py -v`
Expected: 5 passed

- [ ] **Step 7: Commit**

```bash
git add src/vba/contract contracts tests/unit/test_contract.py
git commit -m "feat: contract schema, loader, and acceptance gate"
```

---

## Task 3: Perception — the enumerated element set

**Spec:** 3.1, 3.3, 4.3.

The model never sees raw HTML and never emits a selector or a coordinate. It sees a numbered list of elements and picks one by index. This task builds that list.

**Files:**
- Create: `src/vba/perceive/__init__.py`, `src/vba/perceive/elements.py`, `src/vba/perceive/snapshot.py`
- Test: `tests/unit/test_elements.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Element(target_id: int, tag: str, role: str, name: str, element_id: str, name_attr: str, input_type: str, is_submit: bool, selector: str)`
  - `Observation(url: str, epoch: int, elements: list[Element], text: str, fingerprint: str)` (`fingerprint` is filled by Task 4; leave it as `""` here)
  - `EXTRACT_JS: str` (the browser-side extraction script)
  - `async def snapshot(page, epoch: int) -> Observation`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_elements.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vba.perceive'`

- [ ] **Step 3: Implement the element model**

```python
# src/vba/perceive/elements.py
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Element:
    target_id: int
    tag: str
    role: str
    name: str          # accessible name
    element_id: str    # the id attribute, or ""
    name_attr: str     # the name attribute, or ""
    input_type: str    # the type attribute, or ""
    is_submit: bool
    selector: str      # resolved by the extractor; never emitted by the model


@dataclass(frozen=True)
class Observation:
    url: str
    epoch: int
    elements: list[Element]
    text: str
    fingerprint: str = ""

    def by_id(self, target_id: int) -> Element:
        for e in self.elements:
            if e.target_id == target_id:
                return e
        raise KeyError("no element with target_id " + str(target_id))


def elements_from_records(records: list[dict]) -> list[Element]:
    return [Element(target_id=i, **r) for i, r in enumerate(records)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_elements.py -v`
Expected: 3 passed

- [ ] **Step 5: Implement the browser-side extractor**

The traversal reaches into shadow roots because the target world attaches one in a custom element, and `elementFromPoint` filtering drops elements hidden behind an overlay.

```python
# src/vba/perceive/snapshot.py
from .elements import Observation, elements_from_records

EXTRACT_JS = """
() => {
  const SEL = ['a','button','input','select','textarea','[role="button"]',
               '[role="link"]','[onclick]','[tabindex="0"]'];

  function queryDeep(root) {
    const out = Array.from(root.querySelectorAll(SEL.join(',')));
    for (const el of Array.from(root.querySelectorAll('*'))) {
      if (el.shadowRoot) out.push(...queryDeep(el.shadowRoot));
    }
    return out;
  }

  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  }

  function topmost(el) {
    const r = el.getBoundingClientRect();
    const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
    if (cx < 0 || cy < 0 || cx > innerWidth || cy > innerHeight) return false;
    const top = document.elementFromPoint(cx, cy);
    return !!top && (el === top || el.contains(top) || top.contains(el));
  }

  function accName(el) {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
    if (el.id) {
      const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab) return lab.innerText.trim();
    }
    const own = el.closest('label');
    if (own && el.tagName !== 'LABEL') {
      return own.innerText.trim();
    }
    return (el.innerText || el.value || el.placeholder || '').trim();
  }

  function uniq(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    if (el.getAttribute('name')) {
      return el.tagName.toLowerCase() + '[name="' + el.getAttribute('name') + '"]';
    }
    const p = el.parentElement;
    if (!p) return el.tagName.toLowerCase();
    const same = Array.from(p.children).filter(c => c.tagName === el.tagName);
    return uniq(p) + ' > ' + el.tagName.toLowerCase()
           + ':nth-of-type(' + (same.indexOf(el) + 1) + ')';
  }

  const seen = new Set();
  const out = [];
  for (const el of queryDeep(document)) {
    if (seen.has(el) || !visible(el) || !topmost(el)) continue;
    seen.add(el);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    out.push({
      tag: tag,
      role: el.getAttribute('role') || (tag === 'a' ? 'link'
            : tag === 'button' ? 'button'
            : tag === 'select' ? 'combobox'
            : type === 'checkbox' ? 'checkbox' : 'textbox'),
      name: accName(el),
      element_id: el.id || '',
      name_attr: el.getAttribute('name') || '',
      input_type: type,
      is_submit: (tag === 'button' && type !== 'button')
                 || (tag === 'input' && type === 'submit'),
      selector: uniq(el),
    });
  }
  return out;
}
"""


async def snapshot(page, epoch: int) -> Observation:
    records = await page.evaluate(EXTRACT_JS)
    return Observation(
        url=page.url,
        epoch=epoch,
        elements=elements_from_records(records),
        text=await page.inner_text("body"),
    )
```

- [ ] **Step 6: Commit**

```bash
git add src/vba/perceive tests/unit/test_elements.py
git commit -m "feat: enumerated element set with shadow traversal and overlay filtering"
```

---

## Task 4: The structural fingerprint

**Spec:** 6.2. **Read the Task 1 finding before starting.**

A whole-DOM hash mints one fingerprint per provider, so no fix is ever reused and the memory demonstration dies on the first run. The fingerprint must be identical across providers on one layout, and different across layouts.

**Files:**
- Create: `src/vba/perceive/fingerprint.py`
- Modify: `src/vba/perceive/snapshot.py` (fill in `fingerprint`)
- Test: `tests/unit/test_fingerprint.py`

**Interfaces:**
- Consumes: `Observation`, `Element` from Task 3.
- Produces:
  - `normalize_url(url: str) -> str`
  - `form_signature(elements: list[Element]) -> str`
  - `fingerprint(contract: str, step_key: str, url: str, elements: list[Element]) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_fingerprint.py
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_fingerprint.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vba.perceive.fingerprint'`

- [ ] **Step 3: Implement the fingerprint**

```python
# src/vba/perceive/fingerprint.py
import hashlib
import json
import re
from urllib.parse import urlsplit

from .elements import Element

# Path segments that are identifiers get templated. Extend deliberately, not casually:
# an over-eager rule collapses genuinely different pages into one fingerprint.
_ID_SEGMENT = re.compile(r"^\d{6,}$")


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    segs = ["{id}" if _ID_SEGMENT.match(s) else s for s in parts.path.split("/")]
    return parts.netloc + "/".join(segs)


def form_signature(elements: list[Element]) -> str:
    """Attribute-level, not accessible names.

    Named controls contribute (tag, name attribute, type), which are value-free and
    therefore identical across entities. Buttons contribute (id, accessible name),
    which is what discriminates layouts whose named inputs are identical.
    """
    named = sorted(
        (e.tag, e.name_attr, e.input_type) for e in elements if e.name_attr
    )
    buttons = sorted(
        (e.element_id, e.name) for e in elements if e.is_submit or e.role == "button"
    )
    return json.dumps({"named": named, "buttons": buttons}, sort_keys=True)


def control_set(elements: list[Element]) -> str:
    """Presence, not selection state. Which option is currently chosen is state."""
    return json.dumps(sorted({(e.tag, e.role) for e in elements}), sort_keys=True)


def fingerprint(contract: str, step_key: str, url: str, elements: list[Element]) -> str:
    payload = "|".join([
        contract, step_key, normalize_url(url),
        form_signature(elements), control_set(elements),
    ])
    return hashlib.sha256(payload.encode()).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_fingerprint.py -v`
Expected: 5 passed

- [ ] **Step 5: Wire the fingerprint into the snapshot**

In `src/vba/perceive/snapshot.py`, change `snapshot()` to take the contract and step key and fill the field:

```python
async def snapshot(page, epoch: int, contract: str, step_key: str) -> Observation:
    records = await page.evaluate(EXTRACT_JS)
    elements = elements_from_records(records)
    return Observation(
        url=page.url,
        epoch=epoch,
        elements=elements,
        text=await page.inner_text("body"),
        fingerprint=fingerprint(contract, step_key, page.url, elements),
    )
```

Add `from .fingerprint import fingerprint` at the top of the file.

- [ ] **Step 6: Commit**

```bash
git add src/vba/perceive tests/unit/test_fingerprint.py
git commit -m "feat: structural fingerprint, invariant across entities and distinct across layouts"
```

---

## Task 5: The action type, the choke point, and the tier guard

**Spec:** 4.3, 6.5. This is the safety core. Every refusal here gets a red test, because a partition is only real if it fails mechanically.

**Files:**
- Create: `src/vba/act/__init__.py`, `src/vba/act/actions.py`, `src/vba/act/choke.py`
- Create: `src/vba/guard/__init__.py`, `src/vba/guard/tiers.py`
- Test: `tests/unit/test_guard.py`

**Interfaces:**
- Consumes: `Element`, `Observation` from Task 3; `Step` from Task 2.
- Produces:
  - `Action(kind: str, target_id: int, value: str | None, step_key: str, epoch: int)`
  - `ActionContext(step: Step, grant: Grant, observation: Observation, baseline: Baseline | None)`
  - `class GuardRefusal(Exception)` with `.reason: str`
  - `check(action: Action, ctx: ActionContext) -> None` (raises `GuardRefusal`)
  - `async def execute(action: Action, ctx: ActionContext, page, audit) -> None`

`Baseline` is defined in Task 7; until then, the guard only needs `baseline is not None` and `baseline.epoch`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_guard.py
import pytest

from vba.act.actions import Action, ActionContext
from vba.contract.gate import Grant
from vba.contract.schema import Step
from vba.guard.tiers import GuardRefusal, check
from vba.perceive.elements import Observation, elements_from_records


class FakeBaseline:
    def __init__(self, epoch: int = 7):
        self.epoch = epoch


ELEMENTS = elements_from_records([
    {"tag": "a", "role": "link", "name": "open record", "element_id": "",
     "name_attr": "", "input_type": "", "is_submit": False, "selector": "a"},
    {"tag": "button", "role": "button", "name": "Submit enrollment",
     "element_id": "submit-enrollment", "name_attr": "", "input_type": "submit",
     "is_submit": True, "selector": "#submit-enrollment"},
])
OBS = Observation(url="http://h/p/1", epoch=7, elements=ELEMENTS, text="", fingerprint="f")

TIER1 = Step(step_key="provider.open", intent="open", tier=1)
TIER3 = Step(step_key="enrollment.submit", intent="file it", tier=3,
             satisfied_when="oracle.confirmed")
FULL = Grant(max_tier=3, reason="ok")


def _ctx(step, baseline=None, grant=FULL, obs=OBS):
    return ActionContext(step=step, grant=grant, observation=obs, baseline=baseline)


def test_a_click_on_a_submit_control_during_a_tier_1_step_is_refused():
    """Spec 4.3 shaping rule. The resolver called it a click; the element metadata
    says it is a submit. Metadata wins, or a lower-tier step can fire the form."""
    a = Action(kind="click", target_id=1, value=None, step_key="provider.open", epoch=7)
    with pytest.raises(GuardRefusal, match="submit"):
        check(a, _ctx(TIER1))


def test_a_tier_3_act_without_a_baseline_is_refused():
    """Spec 6.5: tier-3 execute requires a baseline handle, so the guard refuses
    without one. This is one of the two structural conjuncts."""
    a = Action(kind="submit", target_id=1, value=None,
               step_key="enrollment.submit", epoch=7)
    with pytest.raises(GuardRefusal, match="baseline"):
        check(a, _ctx(TIER3, baseline=None))


def test_a_tier_3_act_with_a_stale_baseline_is_refused():
    """The baseline must belong to THIS step, not an earlier one."""
    a = Action(kind="submit", target_id=1, value=None,
               step_key="enrollment.submit", epoch=7)
    with pytest.raises(GuardRefusal, match="baseline"):
        check(a, _ctx(TIER3, baseline=FakeBaseline(epoch=3)))


def test_a_tier_3_act_beyond_the_grant_is_refused():
    """Spec 4.2: an on-page or absent oracle caps autonomy at intake."""
    a = Action(kind="submit", target_id=1, value=None,
               step_key="enrollment.submit", epoch=7)
    limited = Grant(max_tier=2, reason="on-page only", propose_only_tiers={3})
    with pytest.raises(GuardRefusal, match="grant"):
        check(a, _ctx(TIER3, baseline=FakeBaseline(), grant=limited))


def test_a_stale_epoch_is_refused_rather_than_translated():
    """Spec 4.3: a target id from a stale observation is refused, never silently
    re-bound, because the element it named may now be a different element."""
    a = Action(kind="click", target_id=0, value=None,
               step_key="provider.open", epoch=3)
    with pytest.raises(GuardRefusal, match="epoch"):
        check(a, _ctx(TIER1))


def test_an_unknown_target_id_is_refused():
    a = Action(kind="click", target_id=99, value=None,
               step_key="provider.open", epoch=7)
    with pytest.raises(GuardRefusal, match="target"):
        check(a, _ctx(TIER1))


def test_the_authorized_tier_3_submit_is_permitted():
    """The control: the guard must not refuse everything."""
    a = Action(kind="submit", target_id=1, value=None,
               step_key="enrollment.submit", epoch=7)
    check(a, _ctx(TIER3, baseline=FakeBaseline(epoch=7)))  # does not raise


def test_a_tier_1_read_is_permitted():
    a = Action(kind="click", target_id=0, value=None,
               step_key="provider.open", epoch=7)
    check(a, _ctx(TIER1))  # does not raise
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vba.act'`

- [ ] **Step 3: Implement the action type**

```python
# src/vba/act/actions.py
from dataclasses import dataclass
from typing import Any, Literal

ActionKind = Literal[
    "click", "fill", "select", "hover", "scroll", "navigate", "submit", "extract", "draw"
]


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    target_id: int
    value: str | None
    step_key: str
    epoch: int


@dataclass(frozen=True)
class ActionContext:
    step: Any        # contract.schema.Step
    grant: Any       # contract.gate.Grant
    observation: Any # perceive.elements.Observation
    baseline: Any    # oracle.delta.Baseline | None
```

- [ ] **Step 4: Implement the guard**

```python
# src/vba/guard/tiers.py
from vba.act.actions import Action, ActionContext


class GuardRefusal(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def check(action: Action, ctx: ActionContext) -> None:
    """Spec 4.3. Called inside execute(), the only path to a side effect.

    Memory-originated and model-originated actions traverse this identically:
    the guard cannot tell them apart, and that is the point.
    """
    obs = ctx.observation

    if action.epoch != obs.epoch:
        raise GuardRefusal(
            "action carries epoch " + str(action.epoch) + " but the current "
            "observation is epoch " + str(obs.epoch) + "; refusing rather than "
            "re-binding a stale target id"
        )

    try:
        element = obs.by_id(action.target_id)
    except KeyError:
        raise GuardRefusal(
            "no element with target id " + str(action.target_id) + " in this observation"
        )

    # The shaping rule. What the resolver called the action does not decide its tier;
    # the element's own metadata does.
    effective_tier = 3 if element.is_submit else ctx.step.tier

    if effective_tier > ctx.grant.max_tier:
        raise GuardRefusal(
            "tier " + str(effective_tier) + " exceeds the grant of tier "
            + str(ctx.grant.max_tier) + ": " + ctx.grant.reason
        )

    if element.is_submit and ctx.step.tier < 3:
        raise GuardRefusal(
            "element " + repr(element.name) + " is a submit control, but step "
            + repr(ctx.step.step_key) + " is tier " + str(ctx.step.tier)
            + "; a lower-tier step must not fire the form"
        )

    if effective_tier == 3:
        if ctx.baseline is None:
            raise GuardRefusal(
                "tier-3 act requires a baseline read taken before the first action "
                "of this step; none is held"
            )
        if ctx.baseline.epoch != obs.epoch:
            raise GuardRefusal(
                "baseline belongs to epoch " + str(ctx.baseline.epoch)
                + " but this step is at epoch " + str(obs.epoch)
                + "; a baseline from another step cannot authorize this act"
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_guard.py -v`
Expected: 8 passed

- [ ] **Step 6: Implement the choke point**

```python
# src/vba/act/choke.py
from vba.guard.tiers import check

from .actions import Action, ActionContext


async def execute(action: Action, ctx: ActionContext, page, audit) -> None:
    """The single path to a side effect. Spec 3.1, 4.3.

    Nothing else in this codebase may call Playwright's mutating methods. If a second
    call site appears, the guard is no longer a partition.
    """
    check(action, ctx)                      # raises GuardRefusal
    element = ctx.observation.by_id(action.target_id)
    audit.action_permitted(action, element, ctx)

    sel = element.selector
    if action.kind in ("click", "submit"):
        await page.click(sel)
    elif action.kind == "fill":
        await page.fill(sel, action.value or "")
    elif action.kind == "select":
        await page.select_option(sel, action.value or "")
    elif action.kind == "hover":
        await page.hover(sel)
    elif action.kind == "navigate":
        await page.goto(action.value or "")
    elif action.kind == "scroll":
        await page.evaluate("(s) => document.querySelector(s).scrollIntoView()", sel)
    else:
        raise ValueError("unsupported action kind: " + action.kind)
```

- [ ] **Step 7: Commit**

```bash
git add src/vba/act src/vba/guard tests/unit/test_guard.py
git commit -m "feat: action type, choke point, and tier guard with mechanical refusals"
```

---

## Task 6: Credential injection and literal scrubbing

**Spec:** 4.4.

The model emits a field reference. The guard resolves the secret and injects it browser-side. Then it scrubs every literal it injected from everything that leaves the process.

**Files:**
- Create: `src/vba/guard/credentials.py`, `src/vba/guard/scrub.py`
- Test: `tests/unit/test_credentials.py`

**Interfaces:**
- Consumes: `Action`, `ActionContext` from Task 5; `Pii` from Task 2.
- Produces:
  - `CredentialVault(values: dict[str, str])` with `get(ref: str, field: str) -> str`
  - `Scrubber()` with `.record(literal: str) -> None` and `.clean(payload: str) -> str`
  - `resolve_fill_value(action: Action, ctx: ActionContext, vault: CredentialVault, scrubber: Scrubber) -> str`
  - `should_screenshot(url: str, pii: Pii) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_credentials.py
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_credentials.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vba.guard.credentials'`

- [ ] **Step 3: Implement the scrubber**

```python
# src/vba/guard/scrub.py
import re


class Scrubber:
    """Removes injected literals from every payload that leaves the process.

    Token boundaries matter: the world mints confirmation numbers as PC-nnnnnn and
    the authenticator code is six digits, so a naive replace would corrupt an audit
    record's confirmation number. Spec 4.4.
    """

    REPLACEMENT = "[redacted]"

    def __init__(self) -> None:
        self._literals: set[str] = set()

    def record(self, literal: str) -> None:
        if literal:
            self._literals.add(literal)

    def clean(self, payload: str) -> str:
        out = payload
        for lit in sorted(self._literals, key=len, reverse=True):
            out = re.sub(r"(?<![\w-])" + re.escape(lit) + r"(?![\w-])",
                         self.REPLACEMENT, out)
        return out
```

- [ ] **Step 4: Implement the vault**

```python
# src/vba/guard/credentials.py
from vba.act.actions import Action, ActionContext
from vba.contract.schema import Pii

from .scrub import Scrubber


class CredentialVault:
    """Values come from the environment. The model never receives one."""

    def __init__(self, values: dict[str, str]):
        self._values = values

    def get(self, ref: str, field: str) -> str:
        key = ref + ":" + field
        if key not in self._values:
            raise KeyError("no credential for " + key)
        return self._values[key]


def resolve_fill_value(
    action: Action, ctx: ActionContext, vault: CredentialVault, scrubber: Scrubber
) -> str:
    """The model emitted a reference like "portal:password". Resolve it here, record
    the literal for scrubbing, and hand the value straight to the browser."""
    raw = action.value or ""
    if ":" not in raw:
        return raw                       # an ordinary value, not a credential
    ref, _, field = raw.partition(":")
    creds = ctx.step.credentials
    if creds is None or creds.ref != ref or field not in creds.fields:
        raise PermissionError(
            "step " + repr(ctx.step.step_key) + " is not authorized to fill "
            + repr(raw) + " under its contract"
        )
    secret = vault.get(ref, field)
    scrubber.record(secret)
    return secret


def should_screenshot(url: str, pii: Pii) -> bool:
    from urllib.parse import urlsplit
    path = urlsplit(url).path or "/"
    return path not in pii.never_screenshot_urls
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_credentials.py -v`
Expected: 5 passed

- [ ] **Step 6: Wire the scrubber into the choke point**

In `src/vba/act/choke.py`, change the `fill` branch so the value is resolved through the vault, and pass the scrubber in:

```python
    elif action.kind == "fill":
        value = resolve_fill_value(action, ctx, vault, scrubber)
        await page.fill(sel, value)
```

Add `vault` and `scrubber` parameters to `execute()`, and import `resolve_fill_value`.

- [ ] **Step 7: Commit**

```bash
git add src/vba/guard src/vba/act tests/unit/test_credentials.py
git commit -m "feat: credential injection by reference and boundary-aware literal scrubbing"
```

---

## Task 7: The oracle client, the baseline, and delta arithmetic

**Spec:** 5.3, 5.4. This is the module that decides outcomes. Nothing else may.

**Files:**
- Create: `src/vba/oracle/__init__.py`, `src/vba/oracle/client.py`, `src/vba/oracle/delta.py`
- Test: `tests/unit/test_delta.py`

**Interfaces:**
- Consumes: `Contract`, `Oracle` from Task 2.
- Produces:
  - `OracleReading(reachable: bool, enrolled: bool, count: int, latest: dict | None, raw: dict | None)`
  - `Baseline(reading: OracleReading, epoch: int)`
  - `class OracleClient` with `async def read(npi: str) -> OracleReading` and `async def read_all() -> list[dict]`
  - `Outcome` enum: `CONFIRMED, DISCREPANCY, MISFILED, DUPLICATED, NOT_ACTED, REJECTED, VERIFIED_NOT_DONE, UNVERIFIABLE, ALREADY_SATISFIED`
  - `PageVerdict` enum: `PASSED, MECHANICAL, REJECTED, INFRASTRUCTURAL` (defined here so `delta.py` has no import cycle with Task 8)
  - `adjudicate(baseline, after, page, expected_identity, page_confirmation, table) -> Outcome`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_delta.py
from vba.oracle.delta import Baseline, Outcome, OracleReading, PageVerdict, adjudicate


def R(count, enrolled=None, latest=None, reachable=True):
    if enrolled is None:
        enrolled = count > 0
    return OracleReading(reachable=reachable, enrolled=enrolled, count=count,
                         latest=latest, raw={})


IDENT = {"npi": "1700000001", "payer": "Aetna"}
GOOD_ROW = {"npi": "1700000001", "payer": "Aetna", "confirmation_id": "PC-000123"}


def _adj(base, after, page, conf="PC-000123", ident=IDENT, table=None):
    return adjudicate(Baseline(base, epoch=1), after, page, ident, conf, table or [])


def test_count_up_by_one_with_matching_identity_and_confirmation_is_confirmed():
    assert _adj(R(0), R(1, latest=GOOD_ROW), PageVerdict.PASSED) is Outcome.CONFIRMED


def test_a_page_success_with_no_new_row_is_a_discrepancy():
    """Spec 5.3 and the planted silent-failure case: the page says submitted and
    nothing posted. This is the requirement the whole project exists for."""
    assert _adj(R(0), R(0), PageVerdict.PASSED) is Outcome.DISCREPANCY


def test_a_confirmation_number_that_appears_under_another_entity_is_misfiled():
    """Spec 5.3: a per-entity read cannot see a record filed under the wrong entity,
    so a discrepancy triggers a whole-table reconciliation."""
    table = [{"npi": "1700000002", "payer": "Aetna", "confirmation_id": "PC-000123"}]
    assert _adj(R(0), R(0), PageVerdict.PASSED, table=table) is Outcome.MISFILED


def test_a_new_row_with_the_wrong_payer_is_misfiled():
    """The failure a literal-valued memory fix would produce."""
    wrong = {"npi": "1700000001", "payer": "Cigna", "confirmation_id": "PC-000123"}
    assert _adj(R(0), R(1, latest=wrong), PageVerdict.PASSED) is Outcome.MISFILED


def test_a_confirmation_number_matching_nothing_is_not_confirmed():
    """Spec 5.3: CONFIRMED requires three agreements, and the third catches a page
    that mints a confirmation number corresponding to nothing."""
    row = {"npi": "1700000001", "payer": "Aetna", "confirmation_id": "PC-999999"}
    assert _adj(R(0), R(1, latest=row), PageVerdict.PASSED) is not Outcome.CONFIRMED


def test_two_new_rows_is_duplicated():
    assert _adj(R(0), R(2, latest=GOOD_ROW), PageVerdict.PASSED) is Outcome.DUPLICATED


def test_an_unchanged_count_after_an_infrastructural_failure_is_verified_not_done():
    """Spec 5.3 and the portal-outage case: the record answered and shows nothing
    posted. Stronger than merely failing to confirm."""
    assert _adj(R(0), R(0), PageVerdict.INFRASTRUCTURAL) is Outcome.VERIFIED_NOT_DONE


def test_an_unchanged_count_after_a_stated_refusal_is_rejected():
    assert _adj(R(0), R(0), PageVerdict.REJECTED) is Outcome.REJECTED


def test_an_unchanged_count_after_a_mechanical_failure_is_not_acted():
    assert _adj(R(0), R(0), PageVerdict.MECHANICAL) is Outcome.NOT_ACTED


def test_an_unreachable_oracle_is_unverifiable_regardless_of_the_page():
    """Spec 5.4: unknown is not the same as verified absent. Misreading this as
    not-enrolled is what leads to a retry and then a duplicate."""
    assert _adj(R(0), R(0, reachable=False), PageVerdict.PASSED) is Outcome.UNVERIFIABLE


def test_an_already_enrolled_baseline_is_already_satisfied():
    """Spec 5.3: never submit when the record already shows the work is done."""
    assert _adj(R(1, latest=GOOD_ROW), R(1, latest=GOOD_ROW),
                PageVerdict.PASSED) is Outcome.ALREADY_SATISFIED
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_delta.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vba.oracle'`

- [ ] **Step 3: Implement the readings and the adjudicator**

```python
# src/vba/oracle/delta.py
from dataclasses import dataclass
from enum import Enum


class Outcome(Enum):
    CONFIRMED = "confirmed"
    DISCREPANCY = "discrepancy"
    MISFILED = "misfiled"
    DUPLICATED = "duplicated"
    NOT_ACTED = "not_acted"
    REJECTED = "rejected"
    VERIFIED_NOT_DONE = "verified_not_done"
    UNVERIFIABLE = "unverifiable"
    ALREADY_SATISFIED = "already_satisfied"


class PageVerdict(Enum):
    PASSED = "passed"
    MECHANICAL = "mechanical"
    REJECTED = "rejected"
    INFRASTRUCTURAL = "infrastructural"


@dataclass(frozen=True)
class OracleReading:
    reachable: bool
    enrolled: bool
    count: int
    latest: dict | None
    raw: dict | None


@dataclass(frozen=True)
class Baseline:
    reading: OracleReading
    epoch: int


def _identity_matches(row: dict | None, expected: dict) -> bool:
    if row is None:
        return False
    return all(str(row.get(k)) == str(v) for k, v in expected.items())


def adjudicate(
    baseline: Baseline,
    after: OracleReading,
    page: PageVerdict,
    expected_identity: dict,
    page_confirmation: str | None,
    table: list[dict],
) -> Outcome:
    """Spec 5.3. Every outcome is a delta against the pre-act baseline.

    Absolute predicates would confirm work the agent never did, because a row left
    over from an earlier run satisfies "enrolled" without the agent having acted.
    """
    if not after.reachable:
        return Outcome.UNVERIFIABLE

    if baseline.reading.reachable and baseline.reading.count > 0:
        return Outcome.ALREADY_SATISFIED

    delta = after.count - baseline.reading.count

    if delta > 1:
        return Outcome.DUPLICATED

    if delta == 1:
        row = after.latest
        if not _identity_matches(row, expected_identity):
            return Outcome.MISFILED
        if page_confirmation and row.get("confirmation_id") != page_confirmation:
            return Outcome.DISCREPANCY
        return Outcome.CONFIRMED

    # delta == 0: nothing posted under the entity we asked about.
    if page is PageVerdict.PASSED:
        # A per-entity read cannot see a record filed under the wrong entity, so
        # reconcile the whole table before concluding.
        if page_confirmation:
            for row in table:
                if row.get("confirmation_id") == page_confirmation:
                    return Outcome.MISFILED
        return Outcome.DISCREPANCY
    if page is PageVerdict.REJECTED:
        return Outcome.REJECTED
    if page is PageVerdict.MECHANICAL:
        return Outcome.NOT_ACTED
    return Outcome.VERIFIED_NOT_DONE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_delta.py -v`
Expected: 11 passed

- [ ] **Step 5: Implement the HTTP client**

```python
# src/vba/oracle/client.py
import httpx

from .delta import OracleReading


class OracleClient:
    """Reads the record store. Never exposed to the model as a tool: if it were,
    the model could decline to call it, which is the failure this project exists
    to prevent. Spec 4.3."""

    def __init__(self, base_url: str, url_template: str, timeout: float = 5.0):
        self._base = base_url.rstrip("/")
        self._template = url_template
        self._timeout = timeout

    def _url(self, npi: str) -> str:
        return self._template.replace("{base}", self._base).replace("{npi}", npi)

    async def read(self, npi: str) -> OracleReading:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.get(self._url(npi))
                r.raise_for_status()
                data = r.json()
        except Exception:
            # Unreachable, refused, malformed: all are "unknown", never "absent".
            return OracleReading(reachable=False, enrolled=False, count=0,
                                 latest=None, raw=None)
        return OracleReading(
            reachable=True,
            enrolled=bool(data.get("enrolled")),
            count=int(data.get("count", 0)),
            latest=data.get("latest"),
            raw=data,
        )

    async def read_all(self) -> list[dict]:
        """The whole-table read used to reconcile a discrepancy (spec 5.3)."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.get(self._base + "/api/sor/enrollments")
                r.raise_for_status()
                return r.json().get("enrollments", [])
        except Exception:
            return []
```

- [ ] **Step 6: Commit**

```bash
git add src/vba/oracle tests/unit/test_delta.py
git commit -m "feat: oracle client, baseline, and delta adjudication with table reconciliation"
```

---

## Task 8: Page verification

**Spec:** 4.1, 5.2. Deterministic by construction. If this needed a model call, a memory hit would spawn a session every step and the cost claim would be false.

**Files:**
- Create: `src/vba/verify/__init__.py`, `src/vba/verify/page.py`
- Test: `tests/unit/test_page_verify.py`

**Interfaces:**
- Consumes: `Postcondition` from Task 2; `Observation` from Task 3; `PageVerdict` from Task 7.
- Produces: `page_verify(step: Step, obs: Observation, http_status: int | None) -> PageVerdict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_page_verify.py
from vba.contract.schema import Postcondition, Step
from vba.oracle.delta import PageVerdict
from vba.perceive.elements import Observation
from vba.verify.page import page_verify


STEP = Step(
    step_key="enrollment.submit", intent="file it", tier=3,
    satisfied_when="oracle.confirmed",
    postconditions=[
        Postcondition(text_present="Submitted successfully"),
        Postcondition(text_absent="Please confirm you have reviewed"),
    ],
)


def _obs(text: str) -> Observation:
    return Observation(url="http://h/p/1", epoch=1, elements=[], text=text,
                       fingerprint="f")


def test_the_expected_text_passes():
    assert page_verify(STEP, _obs("Submitted successfully"), 200) is PageVerdict.PASSED


def test_a_stated_business_refusal_is_rejected_not_mechanical():
    """Spec 5.2: a refusal is a different category from a click that did not land.
    The world bounces this BEFORE writing any record, so nothing was filed."""
    text = "Please confirm you have reviewed this enrollment before submitting."
    assert page_verify(STEP, _obs(text), 200) is PageVerdict.REJECTED


def test_a_5xx_is_infrastructural_and_never_routes_to_resolution():
    """Spec 5.2: without this, a portal outage sends the agent into a resolution
    spiral against an error page."""
    assert page_verify(STEP, _obs("503 - temporarily unavailable"), 503) \
        is PageVerdict.INFRASTRUCTURAL


def test_a_missing_expected_text_with_no_stated_reason_is_mechanical():
    assert page_verify(STEP, _obs("some other page"), 200) is PageVerdict.MECHANICAL


def test_a_step_with_no_postconditions_passes():
    bare = Step(step_key="provider.open", intent="open", tier=1)
    assert page_verify(bare, _obs("anything"), 200) is PageVerdict.PASSED
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_page_verify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vba.verify'`

- [ ] **Step 3: Implement page verification**

```python
# src/vba/verify/page.py
from vba.contract.schema import Step
from vba.oracle.delta import PageVerdict
from vba.perceive.elements import Observation


def page_verify(step: Step, obs: Observation, http_status: int | None) -> PageVerdict:
    """Spec 5.2. Steers the loop; it can never decide whether work posted.

    Three categories, because collapsing them is how an outage becomes a resubmit:
    infrastructural never routes to resolution, a stated refusal carries its reason
    into the next attempt, and a mechanical failure means the act did not land.
    """
    if http_status is not None and http_status >= 500:
        return PageVerdict.INFRASTRUCTURAL

    text = obs.text or ""

    # A stated refusal is checked first: it is a more specific signal than a missing
    # success string, and the two co-occur by construction.
    for pc in step.postconditions:
        if pc.text_absent and pc.text_absent in text:
            return PageVerdict.REJECTED

    for pc in step.postconditions:
        if pc.text_present and pc.text_present not in text:
            return PageVerdict.MECHANICAL

    return PageVerdict.PASSED
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_page_verify.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/vba/verify tests/unit/test_page_verify.py
git commit -m "feat: deterministic page verification with three failure categories"
```

---

## Task 9: Parameter templating and the learned-fix store

**Spec:** 6.1, 6.6. **Read spec 6.1 in full before starting.** The templating rule is the single most reviewed decision in this design, and the obvious implementation of it is dangerous.

Equality is the wrong test. A stored identity that merely *contains* a parameter is equal to nothing, so equality stores it whole, and in a portal whose index lists every entity on every visit that literal re-binds successfully to the **wrong entity**.

**Files:**
- Create: `src/vba/memory/__init__.py`, `src/vba/memory/templating.py`, `src/vba/memory/store.py`
- Test: `tests/unit/test_templating.py`, `tests/unit/test_store.py`

**Interfaces:**
- Consumes: `Element`, `Observation` from Task 3.
- Produces:
  - `template(text: str, bindings: dict[str, str]) -> str`
  - `bind(text: str, bindings: dict[str, str]) -> str`
  - `StoredAction(kind, identity_id, identity_role, identity_name, value, is_submit)`
  - `LearnedFix(fix_id, site, contract, step_key, intent, page_fingerprint, actions, match_mode, action_tier, polarity, provenance, valid_to, ...)` with `still_resolves(obs, bindings) -> bool`
  - `class FixStore` with `lookup`, `write_candidate`, `promote`, `supersede`, `negatives_for`

- [ ] **Step 1: Write the failing templating tests**

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_templating.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vba.memory'`

- [ ] **Step 3: Implement templating**

```python
# src/vba/memory/templating.py
def template(text: str, bindings: dict[str, str]) -> str:
    """Replace occurrences of bound parameter VALUES with their references.

    Substring, not equality. Spec 6.1 explains why at length: an identity that
    contains a parameter alongside unrelated text is equal to nothing, and storing
    it literally lets it re-bind to the wrong entity on a page that lists them all.

    Longest values first, so a short value that is a substring of a longer one does
    not corrupt it.
    """
    if not text:
        return text
    out = text
    for key, value in sorted(bindings.items(), key=lambda kv: len(kv[1]), reverse=True):
        if value:
            out = out.replace(value, "{" + key + "}")
    return out


def bind(text: str, bindings: dict[str, str]) -> str:
    """The inverse: substitute this invocation's values into a stored string."""
    if not text:
        return text
    out = text
    for key, value in bindings.items():
        out = out.replace("{" + key + "}", value)
    return out
```

- [ ] **Step 4: Run templating tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_templating.py -v`
Expected: 6 passed

- [ ] **Step 5: Write the failing store tests**

```python
# tests/unit/test_store.py
import pytest

from vba.memory.store import FixStore, LearnedFix, StoredAction
from vba.perceive.elements import Observation, elements_from_records


def _obs(records, fp="fp-A"):
    return Observation(url="http://h/p/1", epoch=1,
                       elements=elements_from_records(records), text="", fingerprint=fp)


SUBMIT_A = [{"tag": "button", "role": "button", "name": "Submit enrollment",
             "element_id": "submit-enrollment", "name_attr": "", "input_type": "submit",
             "is_submit": True, "selector": "#submit-enrollment"}]
SUBMIT_C = [{"tag": "button", "role": "button", "name": "Place enrollment",
             "element_id": "place-enrollment", "name_attr": "", "input_type": "submit",
             "is_submit": True, "selector": "#place-enrollment"}]

FIX_A = [StoredAction(kind="submit", identity_id="submit-enrollment",
                      identity_role="button", identity_name="Submit enrollment",
                      value=None, is_submit=True)]


def _fix(actions=None, fp="fp-A", **kw):
    base = dict(site="s", contract="c", step_key="enrollment.submit",
                intent="file it", page_fingerprint=fp,
                actions=actions or FIX_A, match_mode="exact_identity",
                action_tier=3, polarity="positive", provenance="eval_promoted")
    base.update(kw)
    return LearnedFix.new(**base)


def test_a_fix_resolves_when_its_identity_is_present():
    assert _fix().still_resolves(_obs(SUBMIT_A), {}) is True


def test_a_fix_does_not_resolve_when_the_control_was_renamed():
    """Spec 6.4: an id that survives with a changed accessible name is a miss."""
    assert _fix().still_resolves(_obs(SUBMIT_C), {}) is False


def test_a_changed_accessible_name_alone_is_a_miss():
    renamed = [dict(SUBMIT_A[0], name="Submit enrollment now")]
    assert _fix().still_resolves(_obs(renamed), {}) is False


def test_one_current_positive_fix_per_step_but_many_negatives(tmp_path):
    """Spec 6.1: the unique index is scoped to positive polarity."""
    store = FixStore(tmp_path / "m.db")
    store.write_candidate(_fix())
    store.write_candidate(_fix(polarity="negative", failure_mode="review required"))
    store.write_candidate(_fix(polarity="negative", failure_mode="wrong control"))
    assert len(store.negatives_for("s", "c", "enrollment.submit")) == 2


def test_writing_a_second_positive_fix_supersedes_the_first(tmp_path):
    """Spec 6.6: the insert path treats the conflict as a supersede, not an error."""
    store = FixStore(tmp_path / "m.db")
    first = _fix(fp="fp-B")
    store.write_candidate(first)
    store.promote(first.fix_id)
    second = _fix(fp="fp-C")
    store.write_candidate(second)
    current = store.lookup("s", "c", "enrollment.submit")
    assert current.fix_id == second.fix_id
    assert store.get(first.fix_id).valid_to is not None


def test_lookup_is_by_step_key_not_by_fingerprint(tmp_path):
    """Spec 5.1: keying on the fingerprint makes a stale fix a SILENT miss,
    indistinguishable from having no memory at all. The caller compares."""
    store = FixStore(tmp_path / "m.db")
    fix = _fix(fp="fp-B")
    store.write_candidate(fix)
    store.promote(fix.fix_id)
    found = store.lookup("s", "c", "enrollment.submit")
    assert found is not None
    assert found.page_fingerprint == "fp-B"


def test_a_candidate_is_not_returned_as_pre_appliable(tmp_path):
    """Spec 6.4: promotion is eval-gated; a candidate is never pre-applied."""
    store = FixStore(tmp_path / "m.db")
    store.write_candidate(_fix(provenance="candidate"))
    assert store.lookup("s", "c", "enrollment.submit") is None
```

- [ ] **Step 6: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_store.py -v`
Expected: FAIL with `ImportError: cannot import name 'FixStore'`

- [ ] **Step 7: Implement the store**

```python
# src/vba/memory/store.py
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from vba.perceive.elements import Observation

from .templating import bind

SCHEMA = """
CREATE TABLE IF NOT EXISTS learned_fix (
  fix_id            TEXT PRIMARY KEY,
  site              TEXT NOT NULL,
  contract          TEXT NOT NULL,
  step_key          TEXT NOT NULL,
  intent            TEXT NOT NULL,
  page_fingerprint  TEXT NOT NULL,
  actions           TEXT NOT NULL,
  match_mode        TEXT NOT NULL,
  action_tier       INTEGER NOT NULL,
  polarity          TEXT NOT NULL DEFAULT 'positive',
  failure_mode      TEXT,
  verif_strength    TEXT NOT NULL DEFAULT 'cross_system',
  trials            INTEGER NOT NULL DEFAULT 0,
  successes         INTEGER NOT NULL DEFAULT 0,
  confidence        REAL NOT NULL DEFAULT 0,
  provenance        TEXT NOT NULL,
  valid_from        TEXT NOT NULL,
  valid_to          TEXT,
  recorded_at       TEXT NOT NULL,
  last_used_at      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_current_positive_fix_per_step
  ON learned_fix (site, contract, step_key)
  WHERE valid_to IS NULL AND polarity = 'positive';
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class StoredAction:
    kind: str
    identity_id: str
    identity_role: str
    identity_name: str
    value: str | None
    is_submit: bool


@dataclass
class LearnedFix:
    fix_id: str
    site: str
    contract: str
    step_key: str
    intent: str
    page_fingerprint: str
    actions: list[StoredAction]
    match_mode: str
    action_tier: int
    polarity: str = "positive"
    failure_mode: str | None = None
    verif_strength: str = "cross_system"
    trials: int = 0
    successes: int = 0
    confidence: float = 0.0
    provenance: str = "candidate"
    valid_from: str = field(default_factory=_now)
    valid_to: str | None = None
    recorded_at: str = field(default_factory=_now)
    last_used_at: str | None = None

    @classmethod
    def new(cls, **kw) -> "LearnedFix":
        return cls(fix_id=str(uuid.uuid4()), **kw)

    def still_resolves(self, obs: Observation, bindings: dict[str, str]) -> bool:
        """Spec 6.4: check the resolution against the intent, not mere existence.

        Bind first, then require an exact match of the bound identity. The residual
        literal in a templated name is what refuses a wrong-entity act.
        """
        for sa in self.actions:
            want_id = bind(sa.identity_id, bindings)
            want_name = bind(sa.identity_name, bindings)
            if not any(
                e.element_id == want_id
                and e.role == sa.identity_role
                and e.name == want_name
                for e in obs.elements
            ):
                return False
        return True


class FixStore:
    def __init__(self, path):
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        c = sqlite3.connect(self._path)
        c.row_factory = sqlite3.Row
        return c

    def write_candidate(self, fix: LearnedFix) -> None:
        """Spec 6.6: a conflicting current positive fix is superseded, not an error."""
        with self._conn() as c:
            if fix.polarity == "positive":
                c.execute(
                    "UPDATE learned_fix SET valid_to = ? WHERE site = ? AND contract = ? "
                    "AND step_key = ? AND polarity = 'positive' AND valid_to IS NULL",
                    (_now(), fix.site, fix.contract, fix.step_key),
                )
            row = asdict(fix)
            row["actions"] = json.dumps([asdict(a) for a in fix.actions])
            cols = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            c.execute("INSERT INTO learned_fix (" + cols + ") VALUES (" + marks + ")",
                      tuple(row.values()))

    def promote(self, fix_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE learned_fix SET provenance = 'eval_promoted' "
                      "WHERE fix_id = ?", (fix_id,))

    def supersede(self, fix_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE learned_fix SET valid_to = ? WHERE fix_id = ?",
                      (_now(), fix_id))

    def _hydrate(self, row) -> LearnedFix:
        d = dict(row)
        d["actions"] = [StoredAction(**a) for a in json.loads(d["actions"])]
        return LearnedFix(**d)

    def get(self, fix_id: str) -> LearnedFix | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM learned_fix WHERE fix_id = ?",
                            (fix_id,)).fetchone()
        return self._hydrate(row) if row else None

    def lookup(self, site: str, contract: str, step_key: str) -> LearnedFix | None:
        """By step_key, never by fingerprint. Spec 5.1: the caller compares
        fingerprints so a stale fix produces a VISIBLE detection event."""
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM learned_fix WHERE site = ? AND contract = ? "
                "AND step_key = ? AND polarity = 'positive' AND valid_to IS NULL "
                "AND provenance = 'eval_promoted'",
                (site, contract, step_key),
            ).fetchone()
        return self._hydrate(row) if row else None

    def negatives_for(self, site: str, contract: str, step_key: str) -> list[LearnedFix]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM learned_fix WHERE site = ? AND contract = ? "
                "AND step_key = ? AND polarity = 'negative' AND valid_to IS NULL",
                (site, contract, step_key),
            ).fetchall()
        return [self._hydrate(r) for r in rows]
```

- [ ] **Step 8: Run store tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_store.py -v`
Expected: 7 passed

- [ ] **Step 9: Commit**

```bash
git add src/vba/memory tests/unit/test_templating.py tests/unit/test_store.py
git commit -m "feat: substring parameter templating and the learned-fix store"
```

---

## Task 10: Capture slicing and confidence

**Spec:** 6.3, 6.4.

The naive slicing rule captures too little in exactly the scenario it exists for. Because the fingerprint excludes control state, the observation *after* ticking a checkbox still matches the entry fingerprint, so "from the last matching observation" drops the tick and the fix fails on every replay while memory appears healthy.

**Files:**
- Create: `src/vba/memory/capture.py`
- Test: `tests/unit/test_capture.py`

**Interfaces:**
- Consumes: `StoredAction` from Task 9; `Observation`, `Element` from Task 3.
- Produces:
  - `Trace(steps: list[tuple[Observation, Action]])`
  - `slice_capture(entry_fingerprint: str, trace: list[tuple[str, object]]) -> list[object]`
  - `to_stored_actions(pairs, bindings) -> list[StoredAction]`
  - `confidence(verif_strength: str, successes: int, trials: int, age_days: float) -> float`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_capture.py
from vba.memory.capture import confidence, slice_capture


ENTRY = "fp-record-B"
BOUNCE = "fp-bounce"


def test_the_flagship_case_slices_to_tick_then_submit():
    """Spec 6.3. The heal trajectory on the layout that added a required checkbox:
    submit, get bounced, go back, tick, submit. The captured fix must be the last
    two actions, and MUST include the tick."""
    trace = [
        (ENTRY,  "click submit"),
        (BOUNCE, "click back"),
        (ENTRY,  "tick reviewed"),
        (ENTRY,  "click confirm-and-submit"),
    ]
    assert slice_capture(ENTRY, trace) == ["tick reviewed", "click confirm-and-submit"]


def test_the_naive_last_matching_rule_would_drop_the_tick():
    """Guards the exact defect. The observation after ticking still matches the
    entry fingerprint, because the fingerprint excludes control state."""
    trace = [
        (ENTRY,  "click submit"),
        (BOUNCE, "click back"),
        (ENTRY,  "tick reviewed"),
        (ENTRY,  "click confirm-and-submit"),
    ]
    captured = slice_capture(ENTRY, trace)
    assert "tick reviewed" in captured, "slicing dropped a required action"


def test_a_clean_run_captures_everything():
    trace = [(ENTRY, "click submit")]
    assert slice_capture(ENTRY, trace) == ["click submit"]


def test_a_trajectory_that_never_returns_to_entry_captures_nothing():
    trace = [(ENTRY, "click submit"), (BOUNCE, "click back")]
    assert slice_capture(ENTRY, trace) == []


def test_confidence_does_not_reach_one_on_a_single_success():
    """Spec 6.4: Laplace smoothing, so one lucky run does not mint trust.
    Confidence ranks and reports; it gates nothing."""
    assert confidence("cross_system", successes=1, trials=1, age_days=0) < 1.0


def test_confidence_decays_with_age():
    fresh = confidence("cross_system", successes=5, trials=5, age_days=0)
    stale = confidence("cross_system", successes=5, trials=5, age_days=90)
    assert stale < fresh


def test_cross_system_verification_scores_above_on_page():
    x = confidence("cross_system", successes=3, trials=3, age_days=0)
    y = confidence("on_page", successes=3, trials=3, age_days=0)
    assert x > y
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_capture.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vba.memory.capture'`

- [ ] **Step 3: Implement slicing and confidence**

```python
# src/vba/memory/capture.py
import math

from vba.perceive.elements import Element

from .store import StoredAction
from .templating import template


def slice_capture(entry_fingerprint: str, trace: list[tuple[str, object]]) -> list[object]:
    """Spec 6.3.

    Capture the action suffix beginning at the START of the final contiguous run of
    observations whose fingerprint matches the step's entry state.

    Formally: find the latest observation that does NOT match entry, and capture from
    the first matching observation after it. The naive "from the last matching
    observation" drops any action that did not change the structural fingerprint,
    which is precisely the required checkbox tick.
    """
    last_mismatch = -1
    for i, (fp, _action) in enumerate(trace):
        if fp != entry_fingerprint:
            last_mismatch = i
    return [action for fp, action in trace[last_mismatch + 1:]]


def to_stored_actions(
    pairs: list[tuple[Element, str, str | None]], bindings: dict[str, str]
) -> list[StoredAction]:
    """Template every stored string against the capture invocation's bindings."""
    out = []
    for element, kind, value in pairs:
        out.append(StoredAction(
            kind=kind,
            identity_id=template(element.element_id, bindings),
            identity_role=element.role,
            identity_name=template(element.name, bindings),
            value=template(value, bindings) if value else None,
            is_submit=element.is_submit,
        ))
    return out


# Weights and the decay constant ship UNCALIBRATED and gate nothing (spec 6.4, 10.2).
W_VERIF, W_SUCCESS, W_RECENCY = 0.4, 0.4, 0.2
TAU_DAYS = 45.0


def confidence(verif_strength: str, successes: int, trials: int, age_days: float) -> float:
    v = {"cross_system": 1.0, "on_page": 0.5}.get(verif_strength, 0.0)
    s = (successes + 1) / (trials + 2)          # Laplace: one lucky run is not trust
    r = math.exp(-age_days / TAU_DAYS)          # staleness is re-verified more eagerly
    return max(0.0, min(1.0, W_VERIF * v + W_SUCCESS * s + W_RECENCY * r))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_capture.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/vba/memory/capture.py tests/unit/test_capture.py
git commit -m "feat: capture slicing that keeps state-only actions, and uncalibrated confidence"
```

---
