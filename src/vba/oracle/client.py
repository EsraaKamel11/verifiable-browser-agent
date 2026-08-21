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
