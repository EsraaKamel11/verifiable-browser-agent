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
        """Spec 8.1. The record has to say enough to re-derive the guard's decision.

        is_submit and fires_form are both here because tier alone stopped being
        sufficient once the contract could exempt a step from the shaping rule
        (spec 4.3). Without them a tier-2 record that fired a form is
        indistinguishable from a tier-2 record that clicked a link, and the one
        place the exemption is exercised would be the one place the audit is
        silent. They are read off the element's own metadata and the step's own
        declaration, not off anything the resolver said.
        """
        self._append(
            "action", step_key=action.step_key, kind=action.kind,
            target=element.element_id or element.name, epoch=action.epoch,
            tier=ctx.step.tier, permitted=True,
            is_submit=bool(getattr(element, "is_submit", False)),
            fires_form=bool(getattr(ctx.step, "fires_form", False)),
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
