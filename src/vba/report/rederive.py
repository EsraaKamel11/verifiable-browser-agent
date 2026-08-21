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
