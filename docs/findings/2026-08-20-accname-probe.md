# Finding: do accessible names absorb field values? (spec 11, open question 1)

**Date:** 2026-08-20
**Probe:** `tools/probe_accname.py`
**Target:** the vendored world's provider record page, `GET /provider/1700000001`, layout A (default), attestation off (default).
**Snapshot tool:** Playwright 1.49.0, Chromium, `page.accessibility.snapshot()` (the CDP-backed accessibility tree, i.e. Chromium's own accname implementation).

## Setup under test

The record page renders the NPI field as:

```html
<label>NPI <input name="npi" id="npi" value="1700000001" readonly/></label>
```

An embedded, read-only text input sitting inside its own `<label>`. Per the accname
algorithm, an embedded control can in principle contribute its *value* to the name
computed for it, which would make the fingerprint's control-set signature vary
per provider if it were built from accessible names.

## Result

The full probe output was piped to `probe_output.json` (deleted after this finding
was recorded, per the task brief; the run itself is reproducible from
`tools/probe_accname.py` against the vendored world). The NPI field's node in that
snapshot was:

```json
{
  "role": "textbox",
  "name": "NPI ",
  "readonly": true,
  "value": "1700000001"
}
```

The node's `name` value, quoted verbatim, is:

```
NPI 
```

(That is the four characters `N`, `P`, `I`, followed by one trailing space --
the label's literal text content, nothing else. There is no trailing-space
typo here; it is exactly what Chromium reported.)

The field's value, `1700000001`, is reported by the accessibility tree as a
**separate `value` property**, not folded into `name`.

## Which branch holds

**Names do not absorb values.** The NPI textbox's accessible name is `"NPI "`,
not `"NPI 1700000001"` or any string containing `1700000001`. The constraint in
spec section 6.2 relaxes: an accessible-name-based fingerprint would not, in
this snapshot tool, mint one fingerprint per provider for this control.

Per the task brief's instruction for this branch: the attribute-level
fingerprint is still built, because it is more robust regardless of this
result (buttons still need id+text to disambiguate identically-named inputs
across layouts, per 6.2's existing argument) -- but its justification here is
**prudence, not necessity**. Section 6.2 of
`docs/superpowers/specs/2026-08-17-verifiable-browser-agent-design.md` has
been updated to record this finding in place of the "must be validated"
placeholder.

## Reproducing

```
.venv/Scripts/python world/run_world.py            # shell 1, leave running
.venv/Scripts/python tools/probe_accname.py > probe_output.json   # shell 2
```

Inspect the `textbox` node with `"name": "NPI "`; in the current layout it is
a direct child of the root `WebArea` node.
