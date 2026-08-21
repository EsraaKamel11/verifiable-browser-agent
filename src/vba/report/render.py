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
