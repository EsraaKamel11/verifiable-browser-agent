"""PayerConnect (staging) - a synthetic payer enrollment portal for Cascade Credentialing.

This is the counterparty's system, simulated. An implementer building automation against it gets a
URL and credentials (see the runtime environment), never this source. harness / client
own this file.

What it deliberately models:
  - Authorized-session automation: login + a fixed staging 2FA code + a mock captcha.
  - The "ugly-portal long tail": the provider record page embeds an iframe, a
    shadow-DOM web component, and a canvas, so perception must do more than read flat DOM.
  - A submit that writes to a backend SYSTEM OF RECORD (system_of_record.py),
    reconcilable via /api/sor/enrollment/{npi}.
  - The trap: provider 1700000005 (Dr. Alan Reese) is silent_fail=True. His submit
    shows "Submitted successfully" but nothing posts to the SoR. On-page success is a lie;
    only the SoR tells the truth. This is why cross-system verification is the real job.
  - Double-submit: a repeat submit without an Idempotency-Key creates a duplicate SoR row.
  - Harness-triggered incidents via /admin: layout A/B (a vendor UI change that breaks naive
    selectors and adds a review checkbox) and portal up/down (forces honest UNVERIFIABLE).
"""
import secrets
from typing import Optional

from fastapi import FastAPI, Form, Request, Header
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import system_of_record as sor
from seed_data import (
    PAYERS,
    PROVIDERS,
    STAGING_2FA_CODE,
    STAGING_PASSWORD,
    STAGING_USERNAME,
    get_provider,
)

app = FastAPI(title="PayerConnect (staging)")

# In-memory session + world state (fine for a single staging process).
SESSIONS: dict[str, dict] = {}
STATE = {"layout": "A", "portal": "up", "attestation": "off"}

# Phase 2. The attestation signature is a control with NO selectable sub-element: a canvas.
# Enumerated accessibility-tree selection cannot express "the middle of the signing area", so an
# agent has to leave the safe path and act on coordinates (or synthesize pointer events), which is
# exactly the vision tier. The server counts pointer samples, so a signature that was never really
# drawn is detectable server-side rather than only on-screen.
MIN_SIGNATURE_POINTS = 8


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _page(title: str, body: str) -> HTMLResponse:
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>__TITLE__</title>"
        "<style>body{font-family:system-ui,Arial,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem}"
        "label{display:block;margin:.5rem 0}input,select,button{font-size:1rem;padding:.4rem}"
        ".card{border:1px solid #ddd;border-radius:10px;padding:1rem;margin:.75rem 0}"
        ".review-actions{border:1px dashed #b58900;border-radius:8px;padding:.75rem;margin-top:1rem}"
        "</style></head><body>__BODY__</body></html>"
    )
    return HTMLResponse(html.replace("__TITLE__", title).replace("__BODY__", body))


def _session(request: Request) -> Optional[dict]:
    sid = request.cookies.get("sid")
    return SESSIONS.get(sid) if sid else None


def _verified(request: Request) -> bool:
    s = _session(request)
    return bool(s and s.get("verified"))


def _payer_options(selected: str) -> str:
    opts = []
    for p in PAYERS:
        sel = " selected" if p == selected else ""
        opts.append("<option value='__V__'__S__>__V__</option>".replace("__V__", p).replace("__S__", sel))
    return "".join(opts)


# --------------------------------------------------------------------------- #
# auth flow
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def login_page():
    body = (
        "<h2>PayerConnect <small>(staging)</small></h2>"
        "<form method='post' action='/login' class='card'>"
        "<label>Email <input name='username' id='username' autocomplete='off'/></label>"
        "<label>Password <input name='password' id='password' type='password'/></label>"
        "<button id='sign-in' type='submit'>Sign in</button>"
        "</form>"
    )
    return _page("Sign in - PayerConnect", body)


@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    if username.strip() == STAGING_USERNAME and password == STAGING_PASSWORD:
        sid = secrets.token_hex(16)
        SESSIONS[sid] = {"verified": False}
        resp = RedirectResponse(url="/verify", status_code=303)
        resp.set_cookie("sid", sid, httponly=True)
        return resp
    return _page("Sign in - PayerConnect",
                 "<p style='color:#b00'>Invalid credentials.</p><p><a href='/'>Back</a></p>")


@app.get("/verify", response_class=HTMLResponse)
def verify_page(request: Request):
    if not _session(request):
        return RedirectResponse(url="/", status_code=303)
    body = (
        "<h2>Two-step verification</h2>"
        "<form method='post' action='/verify' class='card'>"
        "<label>Authenticator code <input name='code' id='otp' autocomplete='off'/></label>"
        "<label><input type='checkbox' name='captcha' id='not-a-robot' value='1'/> I am not a robot</label>"
        "<button id='verify' type='submit'>Verify</button>"
        "</form>"
    )
    return _page("Verify - PayerConnect", body)


@app.post("/verify")
def do_verify(request: Request, code: str = Form(""), captcha: str = Form("")):
    s = _session(request)
    if not s:
        return RedirectResponse(url="/", status_code=303)
    if code.strip() == STAGING_2FA_CODE and captcha == "1":
        s["verified"] = True
        return RedirectResponse(url="/dashboard", status_code=303)
    return _page("Verify - PayerConnect",
                 "<p style='color:#b00'>Code or captcha incorrect.</p><p><a href='/verify'>Back</a></p>")


# --------------------------------------------------------------------------- #
# work surface
# --------------------------------------------------------------------------- #
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    if not _verified(request):
        return RedirectResponse(url="/", status_code=303)
    rows = "".join(
        "<li><a href='/provider/__NPI__'>__NPI__ - __NAME__ (__SPEC__)</a></li>"
        .replace("__NPI__", p["npi"]).replace("__NAME__", p["name"]).replace("__SPEC__", p["specialty"])
        for p in PROVIDERS
    )
    body = f"<h2>Providers pending enrollment</h2><ul>{rows}</ul>"
    return _page("Dashboard - PayerConnect", body)


_RECORD_TEMPLATE = """
<h2>Provider record</h2>
<div class="card">
  <p><strong>__NAME__</strong> &mdash; __SPEC__</p>
  <p>NPI: __NPI__</p>
  <status-badge status="Pending enrollment"></status-badge>
  <script>
    customElements.define('status-badge', class extends HTMLElement {
      connectedCallback() {
        var s = this.getAttribute('status') || 'Unknown';
        var root = this.attachShadow({mode: 'open'});
        root.innerHTML = '<style>.b{padding:2px 8px;border-radius:8px;background:#eef;font-size:.9rem}</style>'
                       + '<span class="b" id="live-status">Status: ' + s + '</span>';
      }
    });
  </script>
  <iframe id="doc-viewer" title="Credentialing document" width="100%" height="70"
    srcdoc="<p style='font-family:sans-serif;font-size:.85rem'>CAQH attestation on file. Malpractice COI valid through 2027.</p>"></iframe>
  <canvas id="sig-pad" width="220" height="50" style="border:1px solid #ccc"></canvas>
</div>
<form method="post" action="/provider/__NPI__/enroll" class="card">
  __SIG_TOP__
  <label>NPI <input name="npi" id="npi" value="__NPI__" readonly/></label>
  <label>Payer <select name="payer" id="payer">__PAYER_OPTIONS__</select></label>
  __SIG_BOTTOM__
  __SUBMIT_BLOCK__
</form>
"""

# The attestation pad. Rendered only when STATE["attestation"] == "on".
# Note there are now TWO canvases on the record page: the decorative `sig-pad` in the card above
# (part of the ugly-portal long tail since day one) and this functional `attestation-pad`. An agent
# that targets "the canvas" rather than the right canvas signs nothing and is bounced by the server.
_SIG_PAD = """
<div class="sig-wrap" id="sig-wrap">
  <p><strong>Attestation.</strong> Sign below to certify this enrollment is accurate.</p>
  <canvas id="attestation-pad" width="SIGW" height="SIGH"
          style="border:1px solid #888;background:#fff;touch-action:none;cursor:crosshair"></canvas>
  <input type="hidden" name="signature_points" id="signature_points" value="0"/>
  <span id="sig-status">not signed</span>
</div>
<script>
(function(){
  var pad = document.getElementById('attestation-pad');
  if (!pad) { return; }
  var ctx = pad.getContext('2d');
  var field = document.getElementById('signature_points');
  var label = document.getElementById('sig-status');
  var drawing = false, points = 0;
  function at(e){ var r = pad.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
  function bump(){
    points += 1;
    field.value = String(points);
    label.textContent = points >= SIGMIN ? 'signed' : ('not signed (' + points + ')');
  }
  pad.addEventListener('pointerdown', function(e){
    drawing = true; var p = at(e); ctx.beginPath(); ctx.moveTo(p[0], p[1]); bump();
  });
  pad.addEventListener('pointermove', function(e){
    if (!drawing) { return; }
    var p = at(e); ctx.lineTo(p[0], p[1]); ctx.stroke(); bump();
  });
  window.addEventListener('pointerup', function(){ drawing = false; });
})();
</script>
"""


def _sig_block(layout: str) -> tuple[str, str]:
    """Return (top_slot, bottom_slot) for the attestation pad.

    Variant C moves the pad ABOVE the fields and resizes it. That is the point: a learned fix that
    stored a raw coordinate is now pointing at empty space, while a learned fix that stored an
    intent ("sign the attestation pad") re-resolves. A coordinate is the most perishable thing a
    memory can hold, so this is where supersede-on-drift earns its keep.
    """
    if STATE["attestation"] != "on":
        return "", ""
    if layout == "C":
        return _SIG_PAD.replace("SIGW", "360").replace("SIGH", "90").replace("SIGMIN", str(MIN_SIGNATURE_POINTS)), ""
    return "", _SIG_PAD.replace("SIGW", "220").replace("SIGH", "60").replace("SIGMIN", str(MIN_SIGNATURE_POINTS))

_SUBMIT_A = '<button id="submit-enrollment" type="submit">Submit enrollment</button>'

_SUBMIT_B = (
    '<div class="review-actions">'
    '<label><input type="checkbox" name="reviewed" id="reviewed" value="1"/> '
    'I have reviewed this enrollment</label>'
    '<button id="confirm-and-submit" type="submit">Confirm and submit enrollment</button>'
    "</div>"
)

# A third vendor redesign, distinct from A and B: the control is renamed and moved
# again, with no review checkbox. Used to prove the agent re-heals a genuinely NEW
# change and its memory supersedes the now-stale fix instead of blindly replaying it.
_SUBMIT_C = (
    '<section class="submit-wrap">'
    "<p>Ready to file this enrollment with the payer.</p>"
    '<button id="place-enrollment" type="submit">Place enrollment</button>'
    "</section>"
)

_SUBMIT_BLOCKS = {"A": _SUBMIT_A, "B": _SUBMIT_B, "C": _SUBMIT_C}


@app.get("/provider/{npi}", response_class=HTMLResponse)
def provider_record(request: Request, npi: str):
    if not _verified(request):
        return RedirectResponse(url="/", status_code=303)
    if STATE["portal"] == "down":
        return _page("Unavailable", "<h2>503 - PayerConnect is temporarily unavailable.</h2>")
    provider = get_provider(npi)
    if not provider:
        return _page("Not found", "<h2>404 - No such provider.</h2>")
    submit_block = _SUBMIT_BLOCKS.get(STATE["layout"], _SUBMIT_A)
    sig_top, sig_bottom = _sig_block(STATE["layout"])
    body = (
        _RECORD_TEMPLATE
        .replace("__NAME__", provider["name"])
        .replace("__SPEC__", provider["specialty"])
        .replace("__NPI__", npi)
        .replace("__PAYER_OPTIONS__", _payer_options(provider["payer"]))
        .replace("__SIG_TOP__", sig_top)
        .replace("__SIG_BOTTOM__", sig_bottom)
        .replace("__SUBMIT_BLOCK__", submit_block)
    )
    return _page(f"{provider['name']} - PayerConnect", body)


@app.post("/provider/{npi}/enroll")
async def enroll(
    request: Request,
    npi: str,
    payer: str = Form(""),
    reviewed: str = Form(""),
    signature_points: str = Form("0"),
    idempotency_key: str = Form(""),
    idempotency_key_header: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    if not _verified(request):
        return RedirectResponse(url="/", status_code=303)
    if STATE["portal"] == "down":
        return HTMLResponse("<h2>503 - PayerConnect is temporarily unavailable.</h2>", status_code=503)
    provider = get_provider(npi)
    if not provider:
        return _page("Not found", "<h2>404 - No such provider.</h2>")

    # Layout B added a mandatory review step. A naive agent that only learned
    # variant A will miss it and get bounced here (a self-heal trigger).
    if STATE["layout"] == "B" and reviewed != "1":
        return _page(
            "Review required",
            "<h2>Please confirm you have reviewed this enrollment before submitting.</h2>"
            f"<p><a href='/provider/{npi}'>Back</a></p>",
        )

    # Phase 2: the attestation gate. Rejected server-side, so an agent that "clicked the canvas"
    # without actually drawing, or that signed the wrong canvas, is caught by the record rather
    # than by the screenshot looking plausible.
    if STATE["attestation"] == "on":
        try:
            points = int(signature_points or "0")
        except ValueError:
            points = 0
        if points < MIN_SIGNATURE_POINTS:
            return _page(
                "Attestation required",
                "<h2>An attestation signature is required before this enrollment can be filed.</h2>"
                "<p>Sign the attestation pad on the provider record, then submit again.</p>"
                f"<p><a href='/provider/{npi}'>Back</a></p>",
            )

    key = (idempotency_key_header or idempotency_key or "").strip() or None

    # The trap: silent_fail providers show success but never post to the SoR.
    if not provider["silent_fail"]:
        row, _created = sor.record_enrollment(npi, payer or provider["payer"], key)
        confirmation = row["confirmation_id"]
    else:
        confirmation = f"PC-{secrets.randbelow(1_000_000):06d}"  # a confirmation number that means nothing

    body = (
        "<h2 id='result'>Submitted successfully</h2>"
        f"<div class='card'><p>Enrollment for <strong>{provider['name']}</strong> "
        f"(NPI {npi}) was submitted to {payer or provider['payer']}.</p>"
        f"<p>Confirmation number: <span id='confirmation'>{confirmation}</span></p></div>"
        "<p><a href='/dashboard'>Back to dashboard</a></p>"
    )
    return _page("Submitted - PayerConnect", body)


# --------------------------------------------------------------------------- #
# system-of-record reconciliation API (the truth the agent must verify against)
# --------------------------------------------------------------------------- #
@app.get("/api/sor/enrollment/{npi}")
def sor_enrollment(npi: str):
    row = sor.get_enrollment(npi)
    return JSONResponse(
        {
            "npi": npi,
            "enrolled": row is not None,
            "count": sor.count_enrollments(npi),
            "latest": row,
        }
    )


@app.get("/api/sor/enrollments")
def sor_all():
    rows = sor.list_all()
    return JSONResponse({"total": len(rows), "enrollments": rows})


# --------------------------------------------------------------------------- #
# admin controls (harness-triggered incidents; the agent under test never calls these)
# --------------------------------------------------------------------------- #
@app.post("/admin/layout/{variant}")
def set_layout(variant: str):
    variant = variant.upper()
    if variant not in {"A", "B", "C"}:
        return JSONResponse({"error": "variant must be A, B, or C"}, status_code=400)
    STATE["layout"] = variant
    return {"layout": variant}


@app.post("/admin/portal/{status}")
def set_portal(status: str):
    status = status.lower()
    if status not in {"up", "down"}:
        return JSONResponse({"error": "status must be up or down"}, status_code=400)
    STATE["portal"] = status
    return {"portal": status}


@app.post("/admin/attestation/{status}")
def set_attestation(status: str):
    """Phase 2 switch. Off by default so the Phase 1 arc runs unchanged."""
    status = status.lower()
    if status not in {"on", "off"}:
        return JSONResponse({"error": "status must be on or off"}, status_code=400)
    STATE["attestation"] = status
    return {"attestation": status}


@app.post("/admin/reset")
def admin_reset():
    sor.reset()
    SESSIONS.clear()
    STATE.update(layout="A", portal="up", attestation="off")
    return {"reset": True, "state": STATE}


@app.get("/admin/state")
def admin_state():
    return {"state": STATE, "sor_total": len(sor.list_all())}


@app.get("/healthz")
def healthz():
    return {"ok": True, "state": STATE}
