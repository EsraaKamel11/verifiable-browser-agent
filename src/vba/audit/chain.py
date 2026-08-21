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
