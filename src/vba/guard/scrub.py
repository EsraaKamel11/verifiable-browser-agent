import re


class Scrubber:
    """Removes injected literals from every payload that leaves the process.

    Token boundaries matter: the world mints confirmation numbers as PC-nnnnnn and
    the authenticator code is six digits, so a naive replace would corrupt an audit
    record's confirmation number. Spec 4.4.
    """

    REPLACEMENT = "[redacted]"

    def __init__(self) -> None:
        self._literals: set[str] = set()

    def record(self, literal: str) -> None:
        if literal:
            self._literals.add(literal)

    def clean(self, payload: str) -> str:
        out = payload
        for lit in sorted(self._literals, key=len, reverse=True):
            out = re.sub(r"(?<![\w-])" + re.escape(lit) + r"(?![\w-])",
                         self.REPLACEMENT, out)
        return out
