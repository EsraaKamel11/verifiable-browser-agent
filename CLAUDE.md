# Operating rules for resolution sessions

These rules are not advisory and are enforced in code. They are stated here so a
compacted session still knows them.

- Choose elements by their number from the list you are given. Selectors,
  XPaths, and coordinates are not available and will be refused.
- Submitting a form is permitted only on the submit step, or on a step whose
  contract declares that it fires its own form. Every other attempt is refused by
  the guard, and the attempt is recorded.
- Credential values are never shown to you. Pass the reference you were given.
- You cannot verify whether work posted. That is done for you, after you finish,
  against a source of truth you do not have access to. Do not claim success.
- If the page refuses an action with a stated reason, read the reason and satisfy
  it rather than retrying the same action.
