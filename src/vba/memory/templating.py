# src/vba/memory/templating.py
def template(text: str, bindings: dict[str, str]) -> str:
    """Replace occurrences of bound parameter VALUES with their references.

    Substring, not equality. Spec 6.1 explains why at length: an identity that
    contains a parameter alongside unrelated text is equal to nothing, and storing
    it literally lets it re-bind to the wrong entity on a page that lists them all.

    Longest values first, so a short value that is a substring of a longer one does
    not corrupt it.
    """
    if not text:
        return text
    out = text
    for key, value in sorted(bindings.items(), key=lambda kv: len(kv[1]), reverse=True):
        if value:
            out = out.replace(value, "{" + key + "}")
    return out


def bind(text: str, bindings: dict[str, str]) -> str:
    """The inverse: substitute this invocation's values into a stored string."""
    if not text:
        return text
    out = text
    for key, value in bindings.items():
        out = out.replace("{" + key + "}", value)
    return out
