import os

import pytest

from acme.llm import VertexLlm
from acme.llm.config import load_vertex_config

pytestmark = pytest.mark.live


@pytest.mark.skipif(os.environ.get("LLM_MODE") != "vertex", reason="needs LLM_MODE=vertex")
async def test_global_endpoint_round_trip():
    llm = VertexLlm(load_vertex_config(dict(os.environ)))
    text = await llm.compose_passenger_message(
        {"passenger": "Mariana", "summary": "Flight AC1234 is cancelled; you are rebooked."}
    )
    assert text.strip()
