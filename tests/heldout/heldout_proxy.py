# tests/heldout/heldout_proxy.py
"""A record-store proxy for the held-out cases that the frozen world cannot serve.

Spec 7.3 set the precedent: "The target world has no control that makes the record
store unreachable ... The oracle client is routed through a harness-controlled
proxy." The same reasoning applies twice more in this pass, and the world is part
of the system under test, so it is not edited to make a case possible.

Two controls, both harness-side:

  POST /control/suppress/{npi}
      Reads for that identifier answer "nothing enrolled", and the whole-table read
      omits its rows. This reproduces, at the record boundary, what the world's
      silent-failure provider does at the portal boundary: the page says the work
      posted and the record store says nothing did. The fidelity gap is stated
      rather than hidden. The world's own row IS written, so a third party
      re-deriving against the real store sees a row the run said was absent. What
      the agent sees is identical either way, which is what case 4 is about, but
      the run's verdict is only correct relative to the oracle it was given.

  POST /control/blackhole/{on|off}
      The frozen chaos hook's only lever is this endpoint (see vba/run/chaos.py),
      so it is the endpoint this proxy has to expose to be driven from inside a
      run. Here "on" does not stop answering: it starts answering with a malformed
      200, which is the shape held-out case 1 is about.
"""
import httpx
from fastapi import FastAPI, Response

UPSTREAM = "http://127.0.0.1:8799"

app = FastAPI()
STATE = {"suppressed": set(), "malformed": False}

MALFORMED_BODY = '{"npi": "unknown", "enrolled": true, "count": null, "latest": null}'


@app.post("/control/suppress/{npi}")
def suppress(npi: str):
    STATE["suppressed"].add(npi)
    return {"suppressed": sorted(STATE["suppressed"])}


@app.post("/control/blackhole/{status}")
def set_malformed(status: str):
    STATE["malformed"] = status == "on"
    return {"malformed": STATE["malformed"]}


@app.get("/control/state")
def state():
    return {"suppressed": sorted(STATE["suppressed"]), "malformed": STATE["malformed"]}


def _json(body: str, status: int = 200) -> Response:
    return Response(status_code=status, content=body, media_type="application/json")


@app.get("/api/sor/enrollment/{npi}")
async def enrollment(npi: str):
    if STATE["malformed"]:
        return _json(MALFORMED_BODY)
    if npi in STATE["suppressed"]:
        return _json('{"npi": "' + npi + '", "enrolled": false, "count": 0, '
                     '"latest": null}')
    async with httpx.AsyncClient(timeout=5) as client:
        upstream = await client.get(UPSTREAM + "/api/sor/enrollment/" + npi)
    return Response(status_code=upstream.status_code, content=upstream.content,
                    media_type="application/json")


@app.get("/api/sor/enrollments")
async def enrollments():
    if STATE["malformed"]:
        return _json(MALFORMED_BODY)
    async with httpx.AsyncClient(timeout=5) as client:
        upstream = await client.get(UPSTREAM + "/api/sor/enrollments")
    rows = [r for r in upstream.json().get("enrollments", [])
            if r.get("npi") not in STATE["suppressed"]]
    return {"total": len(rows), "enrollments": rows}
