# Operating rules for resolution sessions

These rules are not advisory and are enforced in code. They are stated here so a
compacted session still knows them.

- Choose elements by their number from the list you are given. Selectors,
  XPaths, and coordinates are not available and will be refused.
- Never attempt to submit a form during a step that is not the submit step.
  The guard refuses it and the attempt is recorded.
- Credential values are never shown to you. Pass the reference you were given.
- You cannot verify whether work posted. That is done for you, after you finish,
  against a source of truth you do not have access to. Do not claim success.
- If the page refuses an action with a stated reason, read the reason and satisfy
  it rather than retrying the same action.
