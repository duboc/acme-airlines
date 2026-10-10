from datetime import time

from acme.llm import Acknowledge, DepartureAfter, FakeLlm, Other


async def test_departure_after():
    r = await FakeLlm().parse_passenger_request("Can I leave after 10am?")
    assert r == DepartureAfter(time=time(10, 0))


async def test_departure_after_pm_and_minutes():
    r = await FakeLlm().parse_passenger_request("anything after 2:30 pm please")
    assert r == DepartureAfter(time=time(14, 30))


async def test_acknowledge():
    assert await FakeLlm().parse_passenger_request("Perfect, thanks!") == Acknowledge()


async def test_other():
    r = await FakeLlm().parse_passenger_request("Where is my bag?")
    assert r == Other(text="Where is my bag?")


async def test_scripted_override():
    llm = FakeLlm(parse_script={"hello": Acknowledge()})
    assert await llm.parse_passenger_request("hello") == Acknowledge()


async def test_compose_is_deterministic():
    llm = FakeLlm()
    ctx = {"passenger": "Mariana", "summary": "Flight AC1234 is cancelled."}
    a = await llm.compose_passenger_message(ctx)
    assert a == await llm.compose_passenger_message(ctx)
    assert "Mariana" in a and "AC1234" in a
