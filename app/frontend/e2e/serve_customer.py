"""E2E customer API: the real Customer agent with FakeLlm and a FIXED clock (22:03 on D).

The clock never moves by itself. The test advances it explicitly with
`POST /e2e/advance?minutes=N` (the 6-minute notification settle time), so runs are deterministic.
Run from app/frontend: `uv run --project ../backend python e2e/serve_customer.py`.
"""

import os
from datetime import timedelta

import uvicorn

from acme.api.app import create_app
from acme.domain.clock import FixedClock
from acme.domain.fixtures import DISRUPTION_DETECTED_AT

os.environ["LLM_MODE"] = "fake"  # before the runtime builds its LLM
clock = FixedClock(DISRUPTION_DETECTED_AT)
app = create_app(clock)


@app.post("/e2e/advance", include_in_schema=False)
async def advance(minutes: int = 6) -> dict[str, str]:
    clock.advance(timedelta(minutes=minutes))
    return {"now": clock.now().isoformat()}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("E2E_CUSTOMER_PORT", "8000")))
