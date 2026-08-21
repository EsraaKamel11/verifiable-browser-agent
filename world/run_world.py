"""Start the PayerConnect staging portal.

Run:  python run_world.py
Serves on http://127.0.0.1:8799 and resets the system of record on each start.

Who runs this: the demo harness or a person driving it. The tested agent does not;
they only ever hit the URL. Keep it running while the exercise is active.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import uvicorn  # noqa: E402
import system_of_record as sor  # noqa: E402

PORT = 8799

if __name__ == "__main__":
    sor.init(reset=True)
    from app import app  # noqa: E402

    print(f"PayerConnect staging on http://127.0.0.1:{PORT}  (SoR reset)")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
