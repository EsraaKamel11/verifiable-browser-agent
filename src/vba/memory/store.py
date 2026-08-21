# src/vba/memory/store.py
import json
import sqlite3
import uuid
from contextlib import contextmanager
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

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self._path)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

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

    def current_positive(self, site: str, contract: str,
                         step_key: str) -> LearnedFix | None:
        """The fix a write would supersede, whatever its provenance. Ruling R4.

        lookup() answers "what may be pre-applied", so it filters on provenance and
        cannot see an unpromoted candidate that a new write is about to replace.
        This answers the different question "what is about to be replaced", so the
        supersede event names the right fix. Read-only; write_candidate still does
        the valid_to write itself.
        """
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM learned_fix WHERE site = ? AND contract = ? "
                "AND step_key = ? AND polarity = 'positive' AND valid_to IS NULL",
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
