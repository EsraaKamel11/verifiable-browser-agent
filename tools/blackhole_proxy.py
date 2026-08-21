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
