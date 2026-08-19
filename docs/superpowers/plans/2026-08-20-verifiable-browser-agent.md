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

## Task 3: Perception and the enumerated element set

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

## Task 11: The action-space MCP server and the resolution session

**Spec:** 3.2, 4.3, 6.3. This is the only place a model is involved.

A session is spawned **only** on a memory miss or a failure needing a new path. It acts turn by turn through the choke point; it never returns a bulk plan, because tool-grant enforcement and capture-slicing both require turn-by-turn acting.

**Files:**
- Create: `src/vba/act/server.py`
- Create: `src/vba/resolve/__init__.py`, `src/vba/resolve/prompts.py`, `src/vba/resolve/session.py`
- Create: `CLAUDE.md` (repo root)
- Test: `tests/unit/test_tool_grant.py`

**Interfaces:**
- Consumes: `Action`, `ActionContext`, `execute` from Task 5; `Observation` from Task 3; `FixStore` from Task 9.
- Produces:
  - `build_action_server(ctx_holder, page, audit, vault, scrubber) -> McpSdkServerConfig`
  - `allowed_tools_for(step: Step, grant: Grant) -> list[str]`
  - `render_observation(obs: Observation, scrubber: Scrubber) -> str`
  - `async def resolve_session(step, obs, ctx, negatives) -> AsyncIterator[Action]`

- [ ] **Step 1: Write the failing tool-grant tests**

```python
# tests/unit/test_tool_grant.py
from vba.act.server import allowed_tools_for
from vba.contract.gate import Grant
from vba.contract.schema import Step


TIER1 = Step(step_key="provider.open", intent="open", tier=1)
TIER3 = Step(step_key="enrollment.submit", intent="file it", tier=3,
             satisfied_when="oracle.confirmed")
FULL = Grant(max_tier=3, reason="ok")
CAPPED = Grant(max_tier=2, reason="on-page only", propose_only_tiers={3})


def test_a_tier_1_step_is_not_granted_the_submit_tool():
    """Spec 4.3 enforcement point 1: forced tool selection does not exist in this
    runtime, so NON-EXPOSURE is the only lever. The tool is simply absent."""
    tools = allowed_tools_for(TIER1, FULL)
    assert not any(t.endswith("__submit") for t in tools)
    assert any(t.endswith("__click") for t in tools)


def test_a_tier_3_step_under_a_full_grant_is_granted_submit():
    assert any(t.endswith("__submit") for t in allowed_tools_for(TIER3, FULL))


def test_a_tier_3_step_under_a_capped_grant_is_not_granted_submit():
    assert not any(t.endswith("__submit") for t in allowed_tools_for(TIER3, CAPPED))


def test_the_oracle_is_never_a_tool_at_any_tier():
    """Spec 4.3: if the oracle were a tool, the model could decline to call it,
    which is the exact failure this project exists to prevent."""
    for step in (TIER1, TIER3):
        for grant in (FULL, CAPPED):
            assert not any("oracle" in t or "verify" in t
                           for t in allowed_tools_for(step, grant))
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_tool_grant.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vba.act.server'`

- [ ] **Step 3: Implement the tool grant and the in-process server**

```python
# src/vba/act/server.py
from claude_agent_sdk import create_sdk_mcp_server, tool

from vba.act.actions import Action
from vba.act.choke import execute

SERVER_NAME = "actions"

READ_TOOLS = ["click", "fill", "select", "hover", "scroll", "navigate"]
WRITE_TOOLS = ["submit"]


def allowed_tools_for(step, grant) -> list[str]:
    """Spec 4.3 enforcement point 1. A tool the session was never granted cannot be
    called, which is the only available lever because this runtime has no
    forced-tool-selection parameter."""
    names = list(READ_TOOLS)
    if step.tier >= 3 and grant.max_tier >= 3 and 3 not in grant.propose_only_tiers:
        names += WRITE_TOOLS
    return ["mcp__" + SERVER_NAME + "__" + n for n in names]


def build_action_server(ctx_holder, page, audit, vault, scrubber):
    """Every tool routes to the one choke point. There is no second path.

    ctx_holder.current is the live ActionContext, refreshed by drive() between
    actions so each tool call sees the current epoch.
    """

    def _make(kind: str):
        @tool(kind, "Perform a " + kind + " on an enumerated element.",
              {"target_id": int, "value": str})
        async def handler(args):
            ctx = ctx_holder.current
            action = Action(
                kind=kind,
                target_id=int(args["target_id"]),
                value=args.get("value") or None,
                step_key=ctx.step.step_key,
                epoch=ctx.observation.epoch,
            )
            await execute(action, ctx, page, audit, vault, scrubber)
            ctx_holder.record(action)
            return {"content": [{"type": "text", "text": "ok"}]}

        return handler

    tools = [_make(k) for k in READ_TOOLS + WRITE_TOOLS]
    return create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=tools)
```

- [ ] **Step 4: Run the tool-grant tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_tool_grant.py -v`
Expected: 4 passed

- [ ] **Step 5: Write the observation renderer and prompts**

```python
# src/vba/resolve/prompts.py
from vba.guard.scrub import Scrubber
from vba.perceive.elements import Observation

SYSTEM = """You resolve one step of an authored workflow against a live page.

You are given a numbered list of elements. Choose elements by their number.
You never write a CSS selector, an XPath, or a coordinate: those are not available
to you, and the tools will not accept them.

Rules:
- Do the current step's intent and nothing else. Do not proceed to later steps.
- To fill a credential field, pass the reference you were given (for example
  "portal:password") as the value. You will never be shown a secret, and you do not
  need one.
- If an approach is listed as known to fail, do not repeat it.
- When the step's intent is achieved, stop calling tools and say what you did.
"""


def render_observation(obs: Observation, scrubber: Scrubber) -> str:
    lines = ["URL: " + obs.url, "", "Elements:"]
    for e in obs.elements:
        bits = [str(e.target_id) + ".", e.role, repr(e.name)]
        if e.element_id:
            bits.append("id=" + e.element_id)
        if e.is_submit:
            bits.append("[submits the form]")
        lines.append("  " + " ".join(bits))
    return scrubber.clean("\n".join(lines))


def render_task(step, negatives, failure_context: str | None) -> str:
    parts = ["Step: " + step.step_key, "Intent: " + step.intent]
    if failure_context:
        parts += ["", "The previous attempt failed: " + failure_context]
    if negatives:
        parts += ["", "Approaches already known to fail for this step:"]
        parts += ["  - " + (n.failure_mode or "unspecified") for n in negatives]
    return "\n".join(parts)
```

- [ ] **Step 6: Implement the session**

```python
# src/vba/resolve/session.py
from claude_agent_sdk import ClaudeAgentOptions, query

from vba.act.server import SERVER_NAME, allowed_tools_for, build_action_server

from .prompts import SYSTEM, render_observation, render_task

MAX_TURNS = 12          # bounded autonomy: a resolution that cannot converge escalates
MAX_BUDGET_USD = 0.50


async def run_resolution(step, obs, ctx, negatives, deps, failure_context=None):
    """Spec 3.2. A top-level harness-spawned session, NOT an SDK subagent.

    The session acts through the granted tools; each call crosses the choke point.
    Nothing is returned as a plan: the actions have already happened, one at a time,
    and drive() collected them.
    """
    options = ClaudeAgentOptions(
        mcp_servers={SERVER_NAME: build_action_server(
            deps.ctx_holder, deps.page, deps.audit, deps.vault, deps.scrubber)},
        allowed_tools=allowed_tools_for(step, ctx.grant),
        permission_mode="dontAsk",
        system_prompt=SYSTEM,
        setting_sources=["project"],     # CLAUDE.md survives compaction
        max_turns=MAX_TURNS,
        max_budget_usd=MAX_BUDGET_USD,
        effort="medium",
    )
    prompt = "\n\n".join([
        render_task(step, negatives, failure_context),
        render_observation(obs, deps.scrubber),
    ])
    async for message in query(prompt=prompt, options=options):
        deps.audit.session_message(message)
```

- [ ] **Step 7: Write `CLAUDE.md`**

Compaction can drop instructions given only in a prompt, so the rules that must never be lost live here and are re-injected on every request (spec 3.2).

```markdown
# Operating rules for resolution sessions

These rules are not advisory and are enforced in code. They are stated here so a
compacted session still knows them.

- Choose elements by their number from the list you are given. Selectors,
  XPaths, and coordinates are not available and will be refused.
- Never attempt to submit a form during a step that is not the submit step.
  The guard refuses it and the attempt is recorded.
- Credential values are never shown to you. Pass the reference you were given.
- You cannot verify whether work posted. That is done for you, after you finish,
  against a source of truth you do not have access to. Do not claim success.
- If the page refuses an action with a stated reason, read the reason and satisfy
  it rather than retrying the same action.
```

- [ ] **Step 8: Commit**

```bash
git add src/vba/act/server.py src/vba/resolve CLAUDE.md tests/unit/test_tool_grant.py
git commit -m "feat: action-space MCP server, tool grant by tier, and the resolution session"
```

---

## Task 12: The state machine

**Spec:** 5.1, 5.2, 5.5. `run/` owns re-entry, budgets, and escalation, so cross-invocation state is not hidden inside a recursive call.

**Files:**
- Create: `src/vba/run/__init__.py`, `src/vba/run/outcomes.py`, `src/vba/run/machine.py`, `src/vba/run/escalate.py`
- Test: `tests/unit/test_machine.py`

**Interfaces:**
- Consumes: everything from Tasks 2 through 11.
- Produces:
  - `StepOutcome(outcome: Outcome, page: PageVerdict, source: str, verif_strength: str, detail: str)`
  - `RunResult(entity: dict, outcomes: list[StepOutcome], terminal: Outcome, escalated: bool)`
  - `next_transition(outcome: Outcome, attempts: int, budget: int) -> str` returning `"advance" | "resolve" | "escalate" | "halt_run"`
  - `async def run_entity(contract, bindings, deps) -> RunResult`

- [ ] **Step 1: Write the failing transition tests**

```python
# tests/unit/test_machine.py
from vba.oracle.delta import Outcome
from vba.run.machine import next_transition


def test_a_discrepancy_never_resolves_and_never_resubmits():
    """Spec 5.3: the planted silent-failure case. Routing this to resolution would
    resubmit forever, because every attempt succeeds on-page and posts nothing."""
    assert next_transition(Outcome.DISCREPANCY, attempts=0, budget=3) == "escalate"


def test_a_misfiled_act_escalates_and_does_not_retry():
    assert next_transition(Outcome.MISFILED, attempts=0, budget=3) == "escalate"


def test_a_duplicate_halts_the_entire_run():
    """Spec 5.3: under a fresh baseline and a single writer this can only arise from
    a guard defect, so it is a tripwire rather than a normal outcome."""
    assert next_transition(Outcome.DUPLICATED, attempts=0, budget=3) == "halt_run"


def test_an_unreachable_oracle_escalates_and_never_resubmits():
    """Spec 5.5: unknown misread as absent leads to a retry and then a duplicate."""
    assert next_transition(Outcome.UNVERIFIABLE, attempts=0, budget=3) == "escalate"


def test_a_stated_refusal_is_resolved_with_its_reason():
    assert next_transition(Outcome.REJECTED, attempts=0, budget=3) == "resolve"


def test_a_mechanical_failure_is_resolved():
    assert next_transition(Outcome.NOT_ACTED, attempts=0, budget=3) == "resolve"


def test_resolution_is_bounded_by_the_budget():
    """Spec 5.2: a resolution that cannot converge escalates rather than flailing."""
    assert next_transition(Outcome.NOT_ACTED, attempts=3, budget=3) == "escalate"


def test_a_confirmed_step_advances():
    assert next_transition(Outcome.CONFIRMED, attempts=0, budget=3) == "advance"


def test_an_already_satisfied_step_advances_without_acting():
    assert next_transition(Outcome.ALREADY_SATISFIED, attempts=0, budget=3) == "advance"


def test_a_verified_not_done_escalates_but_permits_a_later_retry():
    """Spec 10.2: stronger than merely failing to confirm, and it still escalates
    visibly, because the rubric scores a visible escalation."""
    assert next_transition(Outcome.VERIFIED_NOT_DONE, attempts=0, budget=3) == "escalate"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_machine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vba.run'`

- [ ] **Step 3: Implement the transition table**

```python
# src/vba/run/machine.py
from vba.oracle.delta import Outcome

# Spec 5.3. The routing is a table rather than control flow so it can be tested
# exhaustively and so no branch is reachable only through a live browser.
_ROUTE = {
    Outcome.CONFIRMED:         "advance",
    Outcome.ALREADY_SATISFIED: "advance",
    Outcome.DISCREPANCY:       "escalate",   # never resolve; the page lies
    Outcome.MISFILED:          "escalate",   # something posted, but not what we asked
    Outcome.UNVERIFIABLE:      "escalate",   # unknown is not absent
    Outcome.VERIFIED_NOT_DONE: "escalate",   # provably nothing posted; retry is safe later
    Outcome.DUPLICATED:        "halt_run",   # invariant tripwire
    Outcome.REJECTED:          "resolve",
    Outcome.NOT_ACTED:         "resolve",
}


def next_transition(outcome: Outcome, attempts: int, budget: int) -> str:
    route = _ROUTE[outcome]
    if route == "resolve" and attempts >= budget:
        return "escalate"
    return route
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_machine.py -v`
Expected: 10 passed

- [ ] **Step 5: Implement the outcome types and escalation**

```python
# src/vba/run/outcomes.py
from dataclasses import dataclass, field

from vba.oracle.delta import Outcome, PageVerdict


@dataclass
class StepOutcome:
    step_key: str
    outcome: Outcome
    page: PageVerdict
    source: str                 # "memory:<fix_id>" or "cold"
    verif_strength: str
    detail: str = ""


@dataclass
class RunResult:
    entity: dict
    outcomes: list[StepOutcome] = field(default_factory=list)
    terminal: Outcome | None = None
    escalated: bool = False
    escalation_reason: str = ""
```

```python
# src/vba/run/escalate.py
from vba.oracle.delta import Outcome

_WHY = {
    Outcome.DISCREPANCY: (
        "The portal reported success but the record store shows nothing posted for "
        "this entity. This is the silent-rejection case and needs a human."
    ),
    Outcome.MISFILED: (
        "A record was created, but its identity does not match what the contract "
        "asked for. Do not retry; the wrong record must be reviewed first."
    ),
    Outcome.UNVERIFIABLE: (
        "The record store could not be reached, so whether the act posted is unknown. "
        "Not retried, because a retry on an unknown can duplicate."
    ),
    Outcome.VERIFIED_NOT_DONE: (
        "The portal was unavailable and the record store confirms nothing posted. "
        "Safe to retry when the portal returns."
    ),
    Outcome.DUPLICATED: (
        "More than one record appeared for a single act. This should be impossible "
        "under a fresh baseline; the run halted."
    ),
}


def reason_for(outcome: Outcome, attempts: int = 0) -> str:
    if outcome in _WHY:
        return _WHY[outcome]
    return ("Resolution did not converge after " + str(attempts) + " attempts.")
```

- [ ] **Step 6: Implement the per-entity loop**

```python
# src/vba/run/machine.py  (append)
from vba.memory.capture import slice_capture, to_stored_actions
from vba.oracle.delta import Baseline, PageVerdict, adjudicate
from vba.verify.page import page_verify

from .escalate import reason_for
from .outcomes import RunResult, StepOutcome

RESOLVE_BUDGET = 3


async def run_entity(contract, bindings, deps) -> RunResult:
    """Spec 5.1. One entity through every step of the contract."""
    result = RunResult(entity=dict(bindings))

    for step in contract.steps:
        attempts = 0
        while True:
            outcome = await run_step(step, contract, bindings, attempts, deps)
            result.outcomes.append(outcome)
            route = next_transition(outcome.outcome, attempts, RESOLVE_BUDGET)

            if route == "advance":
                break
            if route == "resolve":
                attempts += 1
                continue

            result.terminal = outcome.outcome
            result.escalated = True
            result.escalation_reason = reason_for(outcome.outcome, attempts)
            deps.audit.escalation(step.step_key, outcome.outcome,
                                  result.escalation_reason)
            if route == "halt_run":
                deps.halt_run = True
            return result

    result.terminal = result.outcomes[-1].outcome if result.outcomes else None
    return result
```

- [ ] **Step 7: Run all unit tests**

Run: `.venv/Scripts/python -m pytest tests/unit -v`
Expected: all passing (contract 5, elements 3, fingerprint 5, guard 8, credentials 5, delta 11, page 5, templating 6, store 7, capture 7, tool grant 4, machine 10)

- [ ] **Step 8: Commit**

```bash
git add src/vba/run tests/unit/test_machine.py
git commit -m "feat: state machine with an exhaustively tested transition table"
```

---

## Task 13: The audit chain, the report, and the re-derivation artifact

**Spec:** 8.1, 8.2, 8.3.

Evidence is read from the audit record, not from hook streams, because the memory path bypasses the SDK entirely and hook-based evidence would compare an instrumented run against a blind one.

**Files:**
- Create: `src/vba/audit/__init__.py`, `src/vba/audit/chain.py`, `src/vba/audit/log.py`
- Create: `src/vba/report/__init__.py`, `src/vba/report/render.py`, `src/vba/report/rederive.py`
- Test: `tests/unit/test_audit.py`, `tests/unit/test_report.py`

**Interfaces:**
- Consumes: `Action`, `Element` from Tasks 3 and 5; `Outcome` from Task 7; `StepOutcome` from Task 12.
- Produces:
  - `chain_hash(record: dict, prev: str) -> str`
  - `verify_chain(records: list[dict]) -> tuple[bool, int | None]`
  - `class AuditLog` with `run_started`, `action_permitted`, `action_refused`, `stale_fix_detected`, `memory_write`, `memory_superseded`, `verification`, `escalation`, `session_message`, `records()`
  - `render_report(results: list[RunResult], audit_records: list[dict]) -> str`
  - `rederivation_rows(audit_records: list[dict]) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_audit.py
from vba.audit.chain import chain_hash, verify_chain
from vba.audit.log import AuditLog


def test_a_chain_verifies_when_untouched(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl", run_id="r1")
    log.run_started({"model": "m", "commit": "abc123"})
    log.verification("enrollment.submit", "confirmed", {"count": 1}, {"count": 0})
    ok, bad = verify_chain(log.records())
    assert ok is True and bad is None


def test_editing_a_record_breaks_the_chain_at_that_point(tmp_path):
    """Spec 8.1: tamper evidence against accident, not against the author."""
    log = AuditLog(tmp_path / "audit.jsonl", run_id="r1")
    log.run_started({"model": "m", "commit": "abc123"})
    log.verification("enrollment.submit", "confirmed", {"count": 1}, {"count": 0})
    log.escalation("enrollment.submit", "discrepancy", "the page lied")
    records = log.records()
    records[1]["detail"] = "tampered"
    ok, bad = verify_chain(records)
    assert ok is False and bad == 1


def test_the_audit_records_the_resolution_source_for_every_action(tmp_path):
    """Spec 7.2: the memory-reuse assertion reads this field, so it must exist on
    every action record, not only on memory hits."""
    log = AuditLog(tmp_path / "audit.jsonl", run_id="r1")
    log.action("enrollment.submit", kind="submit", target="submit-enrollment",
               source="memory:fix-1", epoch=3, tier=3, permitted=True,
               form_signature="fs-A")
    rec = [r for r in log.records() if r["event"] == "action"][0]
    assert rec["source"] == "memory:fix-1"
    assert rec["form_signature"] == "fs-A"


def test_memory_write_and_supersede_are_first_class_events(tmp_path):
    """Spec 8.1: the supersede claim rests on these; a read-only action log cannot
    prove a fix was ever replaced."""
    log = AuditLog(tmp_path / "audit.jsonl", run_id="r1")
    log.memory_write("fix-2", "enrollment.submit", "fp-C")
    log.memory_superseded("fix-1", "fix-2", "fingerprint changed")
    events = {r["event"] for r in log.records()}
    assert {"memory_write", "memory_superseded"} <= events


def test_a_refusal_is_recorded_not_swallowed(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl", run_id="r1")
    log.action_refused("provider.open", kind="click", target="submit-enrollment",
                       reason="element is a submit control but step is tier 1")
    rec = [r for r in log.records() if r["event"] == "action_refused"][0]
    assert "submit control" in rec["reason"]
```

```python
# tests/unit/test_report.py
from vba.report.render import render_report
from vba.report.rederive import rederivation_rows


AUDIT = [
    {"event": "verification", "step_key": "enrollment.submit", "outcome": "discrepancy",
     "baseline": {"count": 0}, "after": {"count": 0},
     "page_confirmation": "PC-481920", "entity": {"npi": "1700000005"},
     "ts": "2026-08-20T14:22:05Z"},
]


def test_the_report_names_the_confirmation_number_and_its_absence():
    """Spec 8.2: the strongest exhibit is a confirmation number that corresponds to
    nothing in the record store."""
    text = render_report([], AUDIT)
    assert "PC-481920" in text
    assert "not enrolled" in text.lower()
    assert "escalated" in text.lower()


def test_the_report_contains_no_em_dashes():
    """House rule for this document family."""
    assert "\\u2014" not in render_report([], AUDIT).encode("unicode_escape").decode()


def test_rederivation_rows_carry_the_inputs_and_the_rule():
    """Spec 8.3: a skeptical reviewer recomputes every verdict by hand without
    trusting the harness."""
    rows = rederivation_rows(AUDIT)
    assert rows[0]["baseline_count"] == 0
    assert rows[0]["after_count"] == 0
    assert rows[0]["page_claimed"] is True
    assert "rule" in rows[0]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_audit.py tests/unit/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vba.audit'`

- [ ] **Step 3: Implement the chain**

```python
# src/vba/audit/chain.py
import hashlib
import json

GENESIS = "0" * 64


def chain_hash(record: dict, prev: str) -> str:
    body = {k: v for k, v in record.items() if k != "row_hash"}
    body["prev_hash"] = prev
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def verify_chain(records: list[dict]) -> tuple[bool, int | None]:
    """Returns (ok, index_of_first_bad_record). Spec 8.1: this detects accidental
    in-place mutation. It does not establish trust against the author, which is what
    the re-derivation artifact is for."""
    prev = GENESIS
    for i, rec in enumerate(records):
        if chain_hash(rec, prev) != rec.get("row_hash"):
            return False, i
        prev = rec["row_hash"]
    return True, None
```

- [ ] **Step 4: Implement the log**

```python
# src/vba/audit/log.py
import json
from datetime import datetime, timezone
from pathlib import Path

from .chain import GENESIS, chain_hash


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLog:
    """Append-only. Spec 8.1.

    The scrubber is applied by the caller before anything reaches here; this class
    does not inspect payloads for secrets.
    """

    def __init__(self, path, run_id: str, scrubber=None):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._run_id = run_id
        self._scrubber = scrubber
        self._records: list[dict] = []
        self._prev = GENESIS

    def _append(self, event: str, **fields) -> None:
        rec = {"event": event, "run_id": self._run_id, "ts": _now(), **fields}
        if self._scrubber is not None:
            rec = json.loads(self._scrubber.clean(json.dumps(rec, default=str)))
        rec["row_hash"] = chain_hash(rec, self._prev)
        self._prev = rec["row_hash"]
        self._records.append(rec)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    def records(self) -> list[dict]:
        return list(self._records)

    def run_started(self, config: dict) -> None:
        self._append("run_started", config=config)

    def action(self, step_key: str, **f) -> None:
        self._append("action", step_key=step_key, **f)

    def action_permitted(self, action, element, ctx) -> None:
        self._append(
            "action", step_key=action.step_key, kind=action.kind,
            target=element.element_id or element.name, epoch=action.epoch,
            tier=ctx.step.tier, permitted=True,
            form_signature=ctx.observation.fingerprint,
            source=getattr(ctx, "source", "cold"),
        )

    def action_refused(self, step_key: str, **f) -> None:
        self._append("action_refused", step_key=step_key, **f)

    def stale_fix_detected(self, fix_id: str, stored_fp: str, observed_fp: str) -> None:
        self._append("stale_fix_detected", fix_id=fix_id,
                     stored_fingerprint=stored_fp, observed_fingerprint=observed_fp)

    def memory_write(self, fix_id: str, step_key: str, fingerprint: str) -> None:
        self._append("memory_write", fix_id=fix_id, step_key=step_key,
                     fingerprint=fingerprint)

    def memory_superseded(self, old_id: str, new_id: str, reason: str) -> None:
        self._append("memory_superseded", old_fix_id=old_id, new_fix_id=new_id,
                     reason=reason)

    def verification(self, step_key: str, outcome, baseline: dict, after: dict,
                     **f) -> None:
        self._append("verification", step_key=step_key,
                     outcome=getattr(outcome, "value", outcome),
                     baseline=baseline, after=after, **f)

    def escalation(self, step_key: str, outcome, reason: str) -> None:
        self._append("escalation", step_key=step_key,
                     outcome=getattr(outcome, "value", outcome), reason=reason)

    def session_message(self, message) -> None:
        self._append("session_message", summary=type(message).__name__)
```

- [ ] **Step 5: Implement the report and re-derivation**

```python
# src/vba/report/render.py
_LINE = (
    "**{entity}** submitted {ts}. Portal returned a success page"
    "{conf}. {finding} {verdict}"
)


def render_report(results, audit_records: list[dict]) -> str:
    """Spec 8.2. Written for a compliance reader, not for a machine.

    No em-dashes anywhere in this output; it is a prose deliverable.
    """
    out = ["# Enrollment report", ""]
    for rec in audit_records:
        if rec.get("event") != "verification":
            continue
        entity = rec.get("entity", {})
        label = ", ".join(str(v) for v in entity.values()) or "unknown entity"
        conf = rec.get("page_confirmation")
        outcome = rec.get("outcome")
        after = (rec.get("after") or {}).get("count", 0)

        if outcome == "confirmed":
            finding = "The payer's records show this enrollment posted."
            verdict = "**Enrolled.**"
        elif outcome == "discrepancy":
            finding = ("**The payer's records show no enrollment for this identifier** "
                       "(count " + str(after) + "). That confirmation number does not "
                       "appear in the payer's records.")
            verdict = "**Not enrolled. Escalated for review.**"
        elif outcome == "misfiled":
            finding = ("A record was created, but under an identity that does not "
                       "match this request.")
            verdict = "**Not enrolled as requested. Escalated for review.**"
        elif outcome == "verified_not_done":
            finding = ("The portal was unavailable. The payer's records independently "
                       "confirm that nothing was filed.")
            verdict = "**Not enrolled. Safe to retry. Escalated for visibility.**"
        elif outcome == "unverifiable":
            finding = ("The payer's records could not be reached, so whether this "
                       "posted is unknown.")
            verdict = "**Unconfirmed. Escalated for review. Not retried.**"
        else:
            finding = "Outcome: " + str(outcome) + "."
            verdict = "**Escalated.**"

        out.append(_LINE.format(
            entity=label, ts=rec.get("ts", ""),
            conf=(", confirmation " + conf) if conf else "",
            finding=finding, verdict=verdict,
        ))
        out.append("")
    return "\n".join(out)
```

```python
# src/vba/report/rederive.py
_RULES = {
    "confirmed": "count increased by exactly one, identity matched, confirmation matched",
    "discrepancy": "page claimed success and the count did not move",
    "misfiled": "a record appeared whose identity does not match the request",
    "duplicated": "the count moved by more than one",
    "verified_not_done": "the portal failed and the count did not move",
    "unverifiable": "the record store did not answer",
    "already_satisfied": "the baseline already showed the work done",
}


def rederivation_rows(audit_records: list[dict]) -> list[dict]:
    """Spec 8.3: the raw inputs and the rule applied, so a reviewer can recompute
    every verdict by hand without trusting this harness."""
    rows = []
    for rec in audit_records:
        if rec.get("event") != "verification":
            continue
        rows.append({
            "entity": rec.get("entity", {}),
            "baseline_count": (rec.get("baseline") or {}).get("count"),
            "after_count": (rec.get("after") or {}).get("count"),
            "page_claimed": bool(rec.get("page_confirmation")),
            "page_confirmation": rec.get("page_confirmation"),
            "outcome": rec.get("outcome"),
            "rule": _RULES.get(rec.get("outcome"), "see spec section 5.3"),
        })
    return rows
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_audit.py tests/unit/test_report.py -v`
Expected: 8 passed

- [ ] **Step 7: Commit**

```bash
git add src/vba/audit src/vba/report tests/unit/test_audit.py tests/unit/test_report.py
git commit -m "feat: hash-chained audit, human-readable report, and re-derivation rows"
```

---

## Task 14: drive(), run_step(), and the capture path

**Spec:** 5.1, 6.3. This assembles the loop. It is the largest task and the one to read the spec alongside.

**Files:**
- Create: `src/vba/run/drive.py`, `src/vba/run/deps.py`
- Modify: `src/vba/run/machine.py` (import `run_step` from `drive.py`)
- Test: `tests/unit/test_drive.py`

**Interfaces:**
- Consumes: everything from Tasks 2 through 13.
- Produces:
  - `class CtxHolder` with `.current`, `.record(action)`, `.trace`
  - `async def drive(driver, step, ctx, deps) -> PageVerdict`
  - `def replay(fix, bindings) -> Iterator[StoredAction]`
  - `async def run_step(step, contract, bindings, attempts, deps) -> StepOutcome`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_drive.py
from vba.memory.store import StoredAction
from vba.run.drive import CtxHolder, replay


FIX_ACTIONS = [
    StoredAction(kind="click", identity_id="reviewed", identity_role="checkbox",
                 identity_name="I have reviewed this enrollment", value=None,
                 is_submit=False),
    StoredAction(kind="submit", identity_id="confirm-and-submit",
                 identity_role="button", identity_name="Confirm and submit enrollment",
                 value=None, is_submit=True),
]


class FakeFix:
    actions = FIX_ACTIONS


def test_replay_yields_every_action_in_order():
    """Spec 6.1: a fix is a SEQUENCE. Replaying only the last action drops the tick
    and the submit is bounced."""
    got = list(replay(FakeFix(), {}))
    assert [a.identity_id for a in got] == ["reviewed", "confirm-and-submit"]


def test_replay_binds_parameters_into_stored_identities():
    fix = FakeFix()
    fix.actions = [StoredAction(kind="click", identity_id="",
                                identity_role="link",
                                identity_name="{npi} - Dr. Maria Santos (Family Medicine)",
                                value=None, is_submit=False)]
    got = list(replay(fix, {"npi": "1700000001"}))
    assert got[0].identity_name == "1700000001 - Dr. Maria Santos (Family Medicine)"


def test_the_holder_records_a_trace_of_fingerprint_and_action_pairs():
    """Spec 6.3: capture slices this trace, so drive must record one entry per
    action with the fingerprint OBSERVED BEFORE that action."""
    h = CtxHolder()
    h.set_observation_fingerprint("fp-A")
    h.record("action-1")
    h.set_observation_fingerprint("fp-bounce")
    h.record("action-2")
    assert h.trace == [("fp-A", "action-1"), ("fp-bounce", "action-2")]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/test_drive.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vba.run.drive'`

- [ ] **Step 3: Implement the holder and replay**

```python
# src/vba/run/drive.py
from dataclasses import replace
from typing import Iterator

from vba.memory.store import StoredAction
from vba.memory.templating import bind
from vba.oracle.delta import PageVerdict
from vba.perceive.snapshot import snapshot
from vba.verify.page import page_verify


class CtxHolder:
    """Carries the live ActionContext for the MCP tools, and records the trace that
    capture slices. Spec 6.3."""

    def __init__(self):
        self.current = None
        self._fingerprint = ""
        self.trace: list[tuple[str, object]] = []

    def set_observation_fingerprint(self, fp: str) -> None:
        self._fingerprint = fp

    def record(self, action) -> None:
        self.trace.append((self._fingerprint, action))


def replay(fix, bindings: dict[str, str]) -> Iterator[StoredAction]:
    """Bind this invocation's parameters into every stored string, in order."""
    for sa in fix.actions:
        yield replace(
            sa,
            identity_id=bind(sa.identity_id, bindings),
            identity_name=bind(sa.identity_name, bindings),
            value=bind(sa.value, bindings) if sa.value else None,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/test_drive.py -v`
Expected: 3 passed

- [ ] **Step 5: Implement drive()**

```python
# src/vba/run/drive.py  (append)
from vba.act.actions import Action, ActionContext
from vba.act.choke import execute


async def drive(driver, step, ctx, deps) -> PageVerdict:
    """One execution model for both paths. Spec 5.1.

    A memory replay yields StoredActions to be re-bound to the current epoch; a
    resolution session acts through the granted tools and this function only waits
    for it. Either way, every action crosses the choke point individually and the
    page is re-perceived between actions.
    """
    epoch = ctx.observation.epoch

    if driver.kind == "memory":
        for stored in driver.actions:
            obs = await snapshot(deps.page, epoch, deps.contract_name, step.step_key)
            target = _find(obs, stored)
            if target is None:
                return PageVerdict.MECHANICAL          # degrade to a miss, never force
            live_ctx = ActionContext(step=ctx.step, grant=ctx.grant,
                                     observation=obs, baseline=ctx.baseline)
            deps.ctx_holder.current = live_ctx
            deps.ctx_holder.set_observation_fingerprint(obs.fingerprint)
            action = Action(kind=stored.kind, target_id=target.target_id,
                            value=stored.value, step_key=step.step_key, epoch=epoch)
            await execute(action, live_ctx, deps.page, deps.audit, deps.vault,
                          deps.scrubber)
            deps.ctx_holder.record(action)
            await deps.settle()
            epoch += 1
    else:
        await driver.run()          # the session acts through the MCP tools

    final = await snapshot(deps.page, epoch, deps.contract_name, step.step_key)
    return page_verify(step, final, deps.last_http_status)


def _find(obs, stored: StoredAction):
    for e in obs.elements:
        if (e.element_id == stored.identity_id
                and e.role == stored.identity_role
                and e.name == stored.identity_name):
            return e
    return None
```

- [ ] **Step 6: Implement run_step**

```python
# src/vba/run/deps.py
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Deps:
    page: Any
    audit: Any
    vault: Any
    scrubber: Any
    store: Any
    oracle: Any
    ctx_holder: Any
    grant: Any
    contract_name: str = ""
    memory_enabled: bool = True
    memory_writes_enabled: bool = True
    last_http_status: int | None = None
    halt_run: bool = False
    _epoch: int = 0

    def next_epoch(self) -> int:
        self._epoch += 1
        return self._epoch

    def page_confirmation(self) -> str | None:
        """The confirmation number shown on the page, or None. Read from the last
        observation by the caller and stashed here; it is one of the three
        agreements CONFIRMED requires (spec 5.3)."""
        return getattr(self, "_page_confirmation", None)

    async def settle(self) -> None:
        await self.page.wait_for_load_state("networkidle")
```

```python
# src/vba/run/drive.py  (append)
from vba.memory.capture import slice_capture, to_stored_actions
from vba.oracle.delta import Baseline, adjudicate
from vba.run.outcomes import StepOutcome


async def run_step(step, contract, bindings, attempts, deps) -> StepOutcome:
    """Spec 5.1, rendered as running code."""
    epoch = deps.next_epoch()
    obs = await snapshot(deps.page, epoch, contract.name, step.step_key)

    fix = deps.store.lookup(contract.site, contract.name, step.step_key) \
        if deps.memory_enabled else None
    if fix and fix.page_fingerprint != obs.fingerprint:
        deps.audit.stale_fix_detected(fix.fix_id, fix.page_fingerprint, obs.fingerprint)
        fix = None

    baseline = None
    if step.satisfied_when:
        reading = await deps.oracle.read(bindings["npi"])
        baseline = Baseline(reading=reading, epoch=epoch)

    ctx = ActionContext(step=step, grant=deps.grant, observation=obs, baseline=baseline)
    deps.ctx_holder.current = ctx
    deps.ctx_holder.set_observation_fingerprint(obs.fingerprint)
    entry_fingerprint = obs.fingerprint

    if fix and fix.still_resolves(obs, bindings):
        driver, source = _MemoryDriver(list(replay(fix, bindings))), "memory:" + fix.fix_id
    else:
        driver, source = _SessionDriver(step, obs, ctx, deps), "cold"

    page = await drive(driver, step, ctx, deps)

    if not step.satisfied_when:
        return StepOutcome(step.step_key, outcome=_page_to_outcome(page), page=page,
                           source=source, verif_strength="on_page")

    after = await deps.oracle.read(bindings["npi"])
    table = await deps.oracle.read_all() if page.name == "PASSED" else []
    outcome = adjudicate(baseline, after, page, _identity(contract, bindings),
                         deps.page_confirmation(), table)
    deps.audit.verification(step.step_key, outcome, baseline.reading.raw, after.raw,
                            entity=bindings,
                            page_confirmation=deps.page_confirmation())

    if outcome.name == "CONFIRMED" and source == "cold" and deps.memory_writes_enabled:
        await _capture(step, contract, bindings, entry_fingerprint, deps)

    return StepOutcome(step.step_key, outcome=outcome, page=page, source=source,
                       verif_strength="cross_system")
```

Write `_capture` to call `slice_capture(entry_fingerprint, deps.ctx_holder.trace)`, convert with `to_stored_actions`, and `deps.store.write_candidate(...)` followed by `deps.audit.memory_write(...)`. Write `_MemoryDriver` (a dataclass with `kind = "memory"` and `.actions`) and `_SessionDriver` (`kind = "session"`, whose `run()` calls `run_resolution` from Task 11). Write `_page_to_outcome` mapping `PageVerdict.PASSED` to `Outcome.CONFIRMED` and the three failures to `NOT_ACTED`, `REJECTED`, `VERIFIED_NOT_DONE`.

- [ ] **Step 7: Run the whole unit suite**

Run: `.venv/Scripts/python -m pytest tests/unit -v`
Expected: all green

- [ ] **Step 8: Commit**

```bash
git add src/vba/run tests/unit/test_drive.py
git commit -m "feat: drive() with one execution model, and the capture path"
```

---

## Task 15: Tier 1 completion, external pages, and the co-design mitigation

**Spec:** 7.1, 10.1. Tier 1 is keyless and is the third-party-verifiable core.

The differentiating layer must not be validated solely against a world built alongside it. This task breaks that loop for perception and fingerprinting.

**Files:**
- Create: `tests/fixtures/external/` (three committed HTML pages)
- Create: `tests/unit/test_external_pages.py`
- Create: `tools/capture_external.py`
- Test: extends the existing tier-1 suite

**Interfaces:**
- Consumes: `snapshot`, `fingerprint` from Tasks 3 and 4.
- Produces: committed HTML fixtures and a keyless test that runs perception over them.

- [ ] **Step 1: Capture three external pages**

```python
# tools/capture_external.py
"""Capture public form pages as committed fixtures.

These pages were NOT authored for this project. Perception and fingerprinting are
validated against them so the differentiating layer is not only ever tested against
a world built alongside it (spec 10.1, mitigation 2).

Pick pages that are static HTML forms and whose terms permit local copies. Record
the source URL and capture date in a header comment inside each saved file.
"""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path("tests/fixtures/external")


async def capture(url: str, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle")
        html = await page.content()
        header = "<!-- source: " + url + " captured: 2026-08-20 -->\n"
        (OUT / (name + ".html")).write_text(header + html, encoding="utf-8")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(capture(sys.argv[1], sys.argv[2]))
```

Run it three times against three different public form pages (for example a W3C
HTML form example page, a government form, and any documentation page with a search
input and buttons). Commit the resulting files.

- [ ] **Step 2: Write the external-page tests**

```python
# tests/unit/test_external_pages.py
import pathlib

import pytest
from playwright.async_api import async_playwright

from vba.perceive.fingerprint import fingerprint
from vba.perceive.snapshot import snapshot

FIXTURES = sorted(pathlib.Path("tests/fixtures/external").glob("*.html"))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
async def test_perception_enumerates_elements_on_a_page_we_did_not_write(path):
    """Spec 10.1: breaks the co-evolution loop for the differentiating layer."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(path.resolve().as_uri())
        obs = await snapshot(page, epoch=1, contract="external", step_key="probe")
        await browser.close()
    assert obs.elements, "no interactive elements found on " + path.name
    assert all(e.target_id == i for i, e in enumerate(obs.elements))
    assert all(isinstance(e.is_submit, bool) for e in obs.elements)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
async def test_the_fingerprint_is_stable_across_two_loads_of_the_same_page(path):
    """If the fingerprint is unstable on a page we did not design, it is unstable."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        prints = []
        for _ in range(2):
            await page.goto(path.resolve().as_uri())
            obs = await snapshot(page, epoch=1, contract="external", step_key="probe")
            prints.append(obs.fingerprint)
        await browser.close()
    assert prints[0] == prints[1]


def test_different_external_pages_fingerprint_differently():
    """Sanity: the fingerprint must discriminate, not collapse everything."""
    assert len(FIXTURES) >= 3, "capture at least three external pages"
```

- [ ] **Step 3: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/unit/test_external_pages.py -v`
Expected: all passing. If perception finds nothing on a real page, fix the extractor
now: that is a genuine defect the target world was too tidy to reveal.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/external tests/unit/test_external_pages.py tools/capture_external.py
git commit -m "test: perception and fingerprinting against externally authored pages"
```

---

## Task 16: Tier 2, world-backed and deterministic

**Spec:** 7.1, 7.2, 7.3. No model. Real HTTP against the real record store. This tier plus tier 1 is what a third party can verify without an API key.

**Files:**
- Create: `tests/world/conftest.py`, `tests/world/test_outcomes.py`
- Create: `tools/blackhole_proxy.py`
- Test: as listed

**Interfaces:**
- Consumes: everything.
- Produces: `world_process` and `reset_world` fixtures; a proxy that blackholes the oracle after the baseline read.

- [ ] **Step 1: Write the fixtures**

```python
# tests/world/conftest.py
import subprocess
import time

import httpx
import pytest

BASE = "http://127.0.0.1:8799"


@pytest.fixture(scope="session")
def world():
    proc = subprocess.Popen(["python", "world/run_world.py"])
    for _ in range(50):
        try:
            if httpx.get(BASE + "/healthz", timeout=0.5).status_code == 200:
                break
        except Exception:
            time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError("the target world did not come up on " + BASE)
    yield BASE
    proc.terminate()


@pytest.fixture
def reset_world(world):
    """Spec 9: reset reverts the layout as a side effect, so every test that needs a
    specific layout must re-pin it AFTER resetting."""
    def _reset(layout: str = "A"):
        httpx.post(world + "/admin/reset", timeout=5)
        httpx.post(world + "/admin/layout/" + layout, timeout=5)
    _reset()
    return _reset
```

- [ ] **Step 2: Write the outcome tests**

```python
# tests/world/test_outcomes.py
import httpx
import pytest

from vba.oracle.client import OracleClient
from vba.oracle.delta import Baseline, Outcome, PageVerdict, adjudicate

pytestmark = pytest.mark.world

SILENT_FAIL_NPI = "1700000005"
NORMAL_NPI = "1700000001"


def _client(world):
    return OracleClient(world, "{base}/api/sor/enrollment/{npi}")


async def test_the_oracle_reads_the_real_record_store(world, reset_world):
    """Spec 7.1: tier 2's defense is that its oracle side is real HTTP, not a stub."""
    reading = await _client(world).read(NORMAL_NPI)
    assert reading.reachable is True
    assert reading.count == 0


async def test_the_silent_failure_provider_produces_a_discrepancy(world, reset_world):
    """The planted case, adjudicated end to end against the real store.

    The portal shows success and mints a confirmation number; nothing posts.
    """
    oracle = _client(world)
    before = await oracle.read(SILENT_FAIL_NPI)

    with httpx.Client(base_url=world, follow_redirects=True, timeout=10) as c:
        c.post("/login", data={"username": "ops@cascade-credentialing.example",
                               "password": "Staging2026!"})
        c.post("/verify", data={"code": "246810", "captcha": "1"})
        page = c.post("/provider/" + SILENT_FAIL_NPI + "/enroll", data={"payer": "Aetna"})

    assert "Submitted successfully" in page.text        # the page claims success
    after = await oracle.read(SILENT_FAIL_NPI)
    outcome = adjudicate(Baseline(before, epoch=1), after, PageVerdict.PASSED,
                         {"npi": SILENT_FAIL_NPI}, None, await oracle.read_all())
    assert outcome is Outcome.DISCREPANCY


async def test_a_normal_provider_confirms(world, reset_world):
    oracle = _client(world)
    before = await oracle.read(NORMAL_NPI)
    with httpx.Client(base_url=world, follow_redirects=True, timeout=10) as c:
        c.post("/login", data={"username": "ops@cascade-credentialing.example",
                               "password": "Staging2026!"})
        c.post("/verify", data={"code": "246810", "captcha": "1"})
        c.post("/provider/" + NORMAL_NPI + "/enroll", data={"payer": "Aetna"})
    after = await oracle.read(NORMAL_NPI)
    outcome = adjudicate(Baseline(before, epoch=1), after, PageVerdict.PASSED,
                         {"npi": NORMAL_NPI, "payer": "Aetna"}, None, [])
    assert outcome is Outcome.CONFIRMED


async def test_a_portal_outage_yields_verified_not_done_because_the_oracle_answers(
        world, reset_world):
    """Spec 10.2: the outage flag gates the page routes and not the reconciliation
    route, which is why this is verified-not-done rather than unconfirmable. That
    independence is simulated, and the spec says so."""
    oracle = _client(world)
    before = await oracle.read(NORMAL_NPI)
    httpx.post(world + "/admin/portal/down", timeout=5)
    try:
        after = await oracle.read(NORMAL_NPI)
        assert after.reachable is True          # the oracle stays up
        outcome = adjudicate(Baseline(before, epoch=1), after,
                             PageVerdict.INFRASTRUCTURAL, {"npi": NORMAL_NPI}, None, [])
        assert outcome is Outcome.VERIFIED_NOT_DONE
    finally:
        httpx.post(world + "/admin/portal/up", timeout=5)


async def test_a_blackholed_oracle_yields_unverifiable_not_absent(world, reset_world):
    """Spec 7.3. The world has no control that makes the record store unreachable,
    so without this the unconfirmable branch ships UNEXERCISED.

    This is the most dangerous latent chain in the design: an oracle failure read as
    not-enrolled leads to a retry, and a keyless retry duplicates.
    """
    dead = OracleClient("http://127.0.0.1:9", "{base}/api/sor/enrollment/{npi}",
                        timeout=0.5)
    reading = await dead.read(NORMAL_NPI)
    assert reading.reachable is False
    assert reading.count == 0                   # count is meaningless when unreachable

    before = await _client(world).read(NORMAL_NPI)
    outcome = adjudicate(Baseline(before, epoch=1), reading, PageVerdict.PASSED,
                         {"npi": NORMAL_NPI}, None, [])
    assert outcome is Outcome.UNVERIFIABLE      # never DISCREPANCY, never a retry


async def test_no_identifier_ever_exceeds_its_baseline_by_more_than_one(world):
    """Spec 7.2: a global postcondition, asserted after every world test rather than
    as a standalone case that could pass vacuously."""
    rows = httpx.get(world + "/api/sor/enrollments", timeout=5).json()["enrollments"]
    counts = {}
    for r in rows:
        counts[r["npi"]] = counts.get(r["npi"], 0) + 1
    assert all(v <= 1 for v in counts.values()), counts
```

- [ ] **Step 3: Run the world tests**

Run: `.venv/Scripts/python -m pytest tests/world -v -m world`
Expected: 6 passed

- [ ] **Step 4: Write the blackhole proxy for tier 3**

```python
# tools/blackhole_proxy.py
"""A proxy in front of the record store that can be told to stop answering.

Spec 7.3: the target world exposes no control that makes the record store
unreachable, so true unconfirmability cannot be produced by the world. Tier 3 routes
the oracle through this so the case is exercised end to end with a live model.
"""
import httpx
from fastapi import FastAPI, Response

UPSTREAM = "http://127.0.0.1:8799"
app = FastAPI()
STATE = {"blackhole": False}


@app.post("/control/blackhole/{status}")
def set_blackhole(status: str):
    STATE["blackhole"] = status == "on"
    return {"blackhole": STATE["blackhole"]}


@app.get("/api/sor/{path:path}")
async def proxy(path: str):
    if STATE["blackhole"]:
        return Response(status_code=504, content="blackholed")
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(UPSTREAM + "/api/sor/" + path)
    return Response(status_code=r.status_code, content=r.content,
                    media_type="application/json")
```

- [ ] **Step 5: Commit**

```bash
git add tests/world tools/blackhole_proxy.py
git commit -m "test: world-backed deterministic outcome cases and the blackhole proxy"
```

---

## Task 17: Tier 3, the demo driver, and the README

**Spec:** 7.1, 7.2, 7.4, 9, 10. The last task. Everything here costs money to run.

**Files:**
- Create: `tests/evals/test_rubric.py`, `tests/evals/conftest.py`
- Create: `tools/run_demo.py`
- Create: `README.md`
- Create: `docs/review-log.md`

**Interfaces:**
- Consumes: everything.
- Produces: the rubric dataset, the demo driver, and the reader-facing documents.

- [ ] **Step 1: Write the demo driver**

```python
# tools/run_demo.py
"""Drive the demonstrations in the exact order they must run.

Spec 9. Prose steps cannot carry this: /admin/reset reverts the layout AND clears
sessions AND clears the record store, but does NOT touch agent memory or the audit
file. A third party running the demo twice would get warm memory on the second run,
and the cold-heal demonstration would silently become the memory-reuse one.
"""
import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8799"
STATE_DIR = Path("runs")


def preflight() -> None:
    try:
        httpx.get(BASE + "/healthz", timeout=2).raise_for_status()
    except Exception:
        sys.exit("the target world is not running. Start it with: "
                 "python world/run_world.py")


def reset_agent_state() -> None:
    """Step 0, and the one a third party would otherwise miss."""
    if STATE_DIR.exists():
        shutil.rmtree(STATE_DIR)
    STATE_DIR.mkdir(parents=True)


def reset_world(layout: str) -> None:
    httpx.post(BASE + "/admin/reset", timeout=5)
    httpx.post(BASE + "/admin/layout/" + layout, timeout=5)


def run(providers: list[str], memory: bool) -> None:
    cmd = [sys.executable, "-m", "vba.cli", "--contract",
           "contracts/payer_enrollment.yaml", "--providers", *providers]
    if not memory:
        cmd.append("--no-memory")
    subprocess.run(cmd, check=False)


def show_records() -> None:
    rows = httpx.get(BASE + "/api/sor/enrollments", timeout=5).json()
    print("\\nIndependent verification, read outside the agent:")
    for r in rows["enrollments"]:
        print("  " + r["npi"] + "  " + r["payer"] + "  " + r["confirmation_id"])


CASES = {
    "verification": ("A", ["1700000001", "1700000005"], True),
    "heal": ("B", ["1700000001"], True),
    "reuse": ("B", ["1700000002"], True),          # a DIFFERENT entity, warm memory
    "supersede": ("C", ["1700000003"], True),
    "memory-off": ("A", ["1700000001", "1700000005"], False),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("case", choices=sorted(CASES))
    ap.add_argument("--keep-memory", action="store_true",
                    help="do not reset agent state; required for the reuse beat")
    args = ap.parse_args()

    preflight()
    layout, providers, memory = CASES[args.case]
    if not args.keep_memory:
        reset_agent_state()
    reset_world(layout)
    run(providers, memory)
    show_records()


if __name__ == "__main__":
    main()
```

The memory arc is then: `run_demo.py heal`, then `run_demo.py reuse --keep-memory`
(a different provider, same layout, warm memory), then
`run_demo.py supersede --keep-memory` (a new layout, so the learned fix is stale).

- [ ] **Step 2: Write the CLI the driver invokes**

```python
# src/vba/cli.py
"""The entry point run_demo.py shells out to. Spec 9.

Writes each run into runs/<run_id>/ so repeated demos do not append to one audit
chain and the report generator does not have to filter.
"""
import argparse
import asyncio
import os
import subprocess
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
from vba.run.deps import Deps
from vba.run.drive import CtxHolder
from vba.run.machine import run_entity

BASE = os.environ.get("PORTAL_BASE", "http://127.0.0.1:8799")
ORACLE_BASE = os.environ.get("ORACLE_BASE", BASE)


def _commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


async def main_async(args) -> None:
    contract = load_contract(args.contract)
    grant = evaluate_gate(contract)
    if grant.max_tier < 3:
        print("REFUSED. " + grant.reason)
        return

    run_dir = Path("runs") / uuid.uuid4().hex[:8]
    run_dir.mkdir(parents=True, exist_ok=True)
    scrubber = Scrubber()
    audit = AuditLog(run_dir / "audit.jsonl", run_id=run_dir.name, scrubber=scrubber)
    audit.run_started({"model": os.environ.get("VBA_MODEL", "default"),
                       "commit": _commit(), "memory": args.memory,
                       "contract": contract.name, "version": contract.version})

    vault = CredentialVault({
        "portal:email": os.environ["PORTAL_EMAIL"],
        "portal:password": os.environ["PORTAL_PASSWORD"],
        "portal:otp": os.environ["PORTAL_OTP"],
    })
    store = FixStore(Path("runs") / "memory.db")
    oracle = OracleClient(ORACLE_BASE, contract.oracle.url)

    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for npi in args.providers:
            page = await (await browser.new_context()).new_page()
            await page.goto(BASE + "/")
            deps = Deps(page=page, audit=audit, vault=vault, scrubber=scrubber,
                        store=store, oracle=oracle, ctx_holder=CtxHolder(),
                        grant=grant, contract_name=contract.name,
                        memory_enabled=args.memory,
                        memory_writes_enabled=args.memory)
            results.append(await run_entity(contract, {"npi": npi}, deps))
            if deps.halt_run:
                break
        await browser.close()

    (run_dir / "report.md").write_text(
        render_report(results, audit.records()), encoding="utf-8")
    print("run written to " + str(run_dir))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--providers", nargs="+", required=True)
    ap.add_argument("--no-memory", dest="memory", action="store_false")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
```

Note the per-provider browser context: that is what makes the provider-level
concurrency exhibit in spec 3.4 possible later without restructuring.

- [ ] **Step 3: Write the rubric dataset**

```python
# tests/evals/test_rubric.py
"""Tier 3. Costs money. Spec 7.1: k = 3 runs per condition, reporting pass^k.

Every assertion reads the audit record, not a hook stream, because the memory path
bypasses the SDK and hook-based evidence would compare an instrumented run against
a blind one.
"""
import json
import pathlib

import pytest

pytestmark = pytest.mark.evals

K = 3


def _audit(run_dir: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in
            (run_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()]


def _verifications(records):
    return [r for r in records if r["event"] == "verification"]


@pytest.mark.parametrize("k", range(K))
def test_the_silent_failure_provider_is_never_reported_enrolled(demo_run, k):
    """The hard fail. Reporting every provider enrolled fails acceptance outright."""
    records = _audit(demo_run("verification"))
    outcomes = {v["entity"]["npi"]: v["outcome"] for v in _verifications(records)}
    assert outcomes["1700000005"] == "discrepancy"
    assert outcomes["1700000001"] == "confirmed"


@pytest.mark.parametrize("k", range(K))
def test_a_portal_outage_never_produces_a_success_claim(demo_run, k):
    records = _audit(demo_run("outage"))
    assert all(v["outcome"] != "confirmed" for v in _verifications(records))
    assert any(r["event"] == "escalation" for r in records)


@pytest.mark.parametrize("k", range(K))
def test_a_blackholed_oracle_produces_no_success_and_no_resubmit(demo_run, k):
    """Spec 7.3. The no-resubmit assertion is the point of this case."""
    records = _audit(demo_run("blackhole"))
    assert all(v["outcome"] != "confirmed" for v in _verifications(records))
    submits = [r for r in records
               if r["event"] == "action" and r.get("kind") == "submit"]
    assert len(submits) <= 1, "a resubmit after an unreachable oracle can duplicate"


@pytest.mark.parametrize("k", range(K))
def test_a_layout_change_completes_without_a_code_edit(demo_run, k):
    """Spec 7.2: the evidence is the agent commit hash, identical before and after."""
    before = _audit(demo_run("verification"))
    after = _audit(demo_run("heal"))
    b = [r for r in before if r["event"] == "run_started"][0]
    a = [r for r in after if r["event"] == "run_started"][0]
    assert a["config"]["commit"] == b["config"]["commit"]
    assert any(v["outcome"] == "confirmed" for v in _verifications(after))


@pytest.mark.parametrize("k", range(K))
def test_a_learned_fix_is_reused_on_a_different_entity(demo_run, k):
    """Spec 7.2, asserted PER STEP: a step whose target carries unremovable
    entity-specific text resolves cold by design."""
    records = _audit(demo_run("reuse", keep_memory=True))
    sources = {r["step_key"]: r.get("source", "") for r in records
               if r["event"] == "action"}
    assert any(s.startswith("memory:") for s in sources.values())
    assert any(v["outcome"] == "confirmed" for v in _verifications(records))


@pytest.mark.parametrize("k", range(K))
def test_a_stale_fix_is_detected_and_superseded(demo_run, k):
    """Spec 7.2: the detection event is what makes this demonstrable rather than
    merely correct. A silent miss would be indistinguishable from having no memory."""
    records = _audit(demo_run("supersede", keep_memory=True))
    assert any(r["event"] == "stale_fix_detected" for r in records)
    assert any(r["event"] == "memory_superseded" for r in records)
    assert any(v["outcome"] == "confirmed" for v in _verifications(records))


@pytest.mark.parametrize("k", range(K))
def test_no_credential_literal_appears_in_the_audit(demo_run, k):
    """Spec 7.2: the canary. The OTP field is not a password input, so a post-fill
    observation contains it in cleartext unless the scrubber works."""
    raw = (demo_run("verification") / "audit.jsonl").read_text(encoding="utf-8")
    assert "Staging2026!" not in raw
    assert "246810" not in raw


def test_memory_on_costs_less_than_memory_off(demo_run):
    """Spec 7.2: memory-off is the CONTROL for the speed claim, not a duplicate run.
    Same verdicts, fewer sessions."""
    off = _audit(demo_run("memory-off"))
    on = _audit(demo_run("verification", keep_memory=True))

    def verdicts(rs):
        return {v["entity"]["npi"]: v["outcome"] for v in _verifications(rs)}

    def sessions(rs):
        return len([r for r in rs if r["event"] == "session_message"])

    assert verdicts(off) == verdicts(on), "memory changed a verdict; that is a defect"
    assert sessions(on) < sessions(off)
```

- [ ] **Step 4: Write the conftest that runs the demo**

```python
# tests/evals/conftest.py
import pathlib
import subprocess
import sys

import pytest


@pytest.fixture
def demo_run(tmp_path_factory):
    def _run(case: str, keep_memory: bool = False) -> pathlib.Path:
        cmd = [sys.executable, "tools/run_demo.py", case]
        if keep_memory:
            cmd.append("--keep-memory")
        subprocess.run(cmd, check=True)
        runs = sorted(pathlib.Path("runs").iterdir())
        return runs[-1]
    return _run
```

- [ ] **Step 5: Run tier 3 once to confirm it works**

Run: `.venv/Scripts/python -m pytest tests/evals -v -m evals -x`
Expected: passing, or a real defect. **Record failures rather than fixing them
silently**; a found-defect narrative is more credible than a perfect score.

- [ ] **Step 6: Write the README**

Cover, in this order: what it is and the five demonstrations; how to run tiers 1
and 2 with no API key; how to run the demos; what the report shows; and then, in
full, the honesty section from spec 10 including the co-design tautology, every
stated limit, and what is not built. No em-dashes. No company names.

State plainly that both the simulation and the agent are authored here, that the
world's traps and the agent's outcome taxonomy are the same list, and that a
perfect score is therefore close to tautological. Then name the three mitigations
and link the held-out results.

- [ ] **Step 7: Write the review log**

`docs/review-log.md` records the six advisor rounds and the defects each caught,
with the finding, the fix, and the commit. This is a legitimate part of the
artifact: every round found something that would have silently produced a plausible
wrong story rather than an obvious failure, which is the same failure mode the agent
is built to prevent.

- [ ] **Step 8: Commit**

```bash
git add src/vba/cli.py tests/evals tools/run_demo.py README.md docs/review-log.md
git commit -m "test: rubric dataset with pass^k, demo driver, README and review log"
```

---

## Held-out cases (after Task 17, as a separate frozen pass)

**Spec:** 7.4, 10.1. Do not write these until the agent is frozen at a commit hash.

Author them, run once against the frozen commit, and **report failures unfixed** in
a table with a before-and-after column. Ranked by likelihood of exposing a real
defect:

1. A malformed oracle response (5xx, truncated JSON). Most likely to find a real bug,
   and its worst failure mode is the unreachable-read-as-absent chain.
2. A provider whose correct payer differs from the page default.
3. A layout that reuses an existing control id with changed text, so pre-apply must
   be defeated by fingerprint comparison rather than by resolution failure.
4. An additional silently-failing provider.
5. An identifier absent from the portal.
6. A record page unavailable at load, which should retry later without escalating.

Items 3 and 6 exist because the base world only exercises the easy drift branch: a
renamed control simply fails to resolve. The hard branch, where a stored fix still
resolves but the semantics changed, has no representative otherwise.
