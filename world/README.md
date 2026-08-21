# Vendored world

This directory is the target simulation: a synthetic payer-enrollment portal
the agent operates against. It is vendored here so the evaluation is
reproducible from this repository alone, without depending on an external
source for the world.

It was authored by the same author as the agent. Spec section 10.1
("Co-design") addresses the conflict of interest this creates directly:
the world's edge cases (the silent-fail trap, layout drift, the attestation
canvas) were designed by the same person building the agent that must catch
them, so the evaluation's credibility rests on the honesty and disclosure
practices in that section, not on the world being adversarially sourced.

## Running it

The world uses flat imports (e.g. `import system_of_record`) and expects to
run as its own process from this directory. It is never imported by
`src/vba`.

```bash
.venv/Scripts/python world/run_world.py
```

It serves on `http://127.0.0.1:8799` and resets its system-of-record database
on every start. Check `/healthz` for a 200 once it is up.

## Files

- `run_world.py` -- entry point; starts the server and resets state.
- `app.py` -- the FastAPI app: login/2FA, dashboard, provider record pages,
  the enrollment submit flow, the system-of-record reconciliation API, and
  `/admin` controls for triggering mid-run incidents (layout change, portal
  down, attestation on/off).
- `system_of_record.py` -- the backend "system of record" the portal's
  on-screen confirmation must be reconciled against; backed by a local
  SQLite file (`sor.db`, not committed -- see `.gitignore`).
- `seed_data.py` -- fictional staging credentials and seeded provider/payer
  data used only by this simulation.
