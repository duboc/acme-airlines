import asyncio

from tests.sim.conftest import call


async def test_hotel_book_returns_reference_and_is_in_world_state(connect, sim):
    async with connect("concierge") as s:
        r = await call(s, "hotel.book", hotel_id="HT8842", nights=1, pax=1,
                       passenger_id="P-MARIANA")  # fmt: skip
    assert r["booking_ref"] and r["hotel_id"] == "HT8842"
    assert sim.partners.hotel_bookings[r["booking_ref"]]["nights"] == 1


async def test_hotel_without_rooms_or_bad_args_is_error(connect, sim):
    async with connect("concierge") as s:
        no_rooms = await s.call_tool("hotel.book", {"hotel_id": "HT0000", "nights": 1, "pax": 1})
        zero = await s.call_tool("hotel.book", {"hotel_id": "HT8842", "nights": 0, "pax": 1})
    assert no_rooms.isError and zero.isError
    assert sim.partners.hotel_bookings == {}


async def test_transfer_and_voucher(connect, sim):
    async with connect("concierge") as s:
        t = await call(s, "transfer.book", origin="SCL", destination="HT8842",
                       at="2026-10-09T23:30:00-03:00")  # fmt: skip
        v = await call(s, "voucher.issue", type="meal", amount="25.00")
        bad = await s.call_tool("voucher.issue", {"type": "spa", "amount": "25"})
        naive = await s.call_tool(
            "transfer.book", {"origin": "A", "destination": "B", "at": "2026-10-09T23:30:00"}
        )
    assert t["transfer_ref"] in sim.partners.transfers
    assert v["voucher_id"] in sim.partners.vouchers
    assert v["amount"] == "25.00" and v["type"] == "meal"
    assert bad.isError and naive.isError


async def test_charge_is_idempotent(connect, sim):
    args = dict(pnr="MSLV42", amount="135.00", currency="USD", idempotency_key="k-1")
    async with connect("payments") as s:
        a = await call(s, "charge", **args)
        b = await call(s, "charge", **args)
    assert a["charge_id"] == b["charge_id"]
    assert a["idempotent_replay"] is False and b["idempotent_replay"] is True
    assert len(sim.partners.charges) == 1


async def test_charge_key_reuse_with_different_params_and_bad_input(connect, sim):
    async with connect("payments") as s:
        await call(s, "charge", pnr="MSLV42", amount="135.00", currency="USD", idempotency_key="k")
        diff = await s.call_tool(
            "charge",
            {"pnr": "MSLV42", "amount": "1.00", "currency": "USD", "idempotency_key": "k"},
        )
        unknown = await s.call_tool(
            "charge",
            {"pnr": "NOPE", "amount": "1", "currency": "USD", "idempotency_key": "z"},
        )
        zero = await s.call_tool(
            "charge",
            {"pnr": "MSLV42", "amount": "0", "currency": "USD", "idempotency_key": "y"},
        )
        cur = await s.call_tool(
            "charge",
            {"pnr": "MSLV42", "amount": "1", "currency": "usd", "idempotency_key": "x"},
        )
    assert diff.isError and unknown.isError and zero.isError and cur.isError
    assert len(sim.partners.charges) == 1


async def test_whatsapp_send_with_buttons_and_transcript(connect, sim):
    async with connect("whatsapp") as s:
        sent = await call(s, "message.send", to="P-MARIANA", body="Accept the offer?",
                          buttons=["Accept", "No thanks"])  # fmt: skip
        no_buttons = await call(s, "message.send", to="P-MARIANA", body="Hello")
        t = await call(s, "message.transcript", to="P-MARIANA")
        other = await call(s, "message.transcript", to="P-OTHER")
    assert sent["message_id"]
    first = t["messages"][0]
    assert first["direction"] == "outbound" and first["buttons"] == ["Accept", "No thanks"]
    assert first["at"] and first["body"] == "Accept the offer?"
    assert t["messages"][1]["buttons"] == [] and no_buttons["message_id"]
    assert other["messages"] == []


async def test_receive_is_in_transcript_and_delivered_to_subscribers(connect, sim):
    got = []
    sim.partners.add_inbound_listener(got.append)
    async with connect("whatsapp") as s:
        await call(s, "message.send", to="P-MARIANA", body="hi")
        await call(s, "message.receive", sender="P-MARIANA", body="Perfect, thanks!")
        t = await call(s, "message.transcript", to="P-MARIANA")
    assert [m["direction"] for m in t["messages"]] == ["outbound", "inbound"]
    assert got[0]["body"] == "Perfect, thanks!" and got[0]["passenger_id"] == "P-MARIANA"


async def test_inbound_long_poll_for_remote_subscribers(connect):
    async with connect("whatsapp") as sub, connect("whatsapp") as pub:
        waiter = asyncio.create_task(call(sub, "message.inbound", cursor=0, timeout_s=5))
        await asyncio.sleep(0.05)
        await call(pub, "message.receive", sender="P-MARIANA", body="after 10am?")
        got = await waiter
        empty = await call(sub, "message.inbound", cursor=got["next_cursor"], timeout_s=0)
    assert got["messages"][0]["body"] == "after 10am?" and got["next_cursor"] == 1
    assert empty["messages"] == []


async def test_registry_contains_partner_servers():
    from acme.sim.app import DEFAULT_REGISTRY

    assert {"concierge", "whatsapp", "payments"} <= set(DEFAULT_REGISTRY)


async def test_charge_void_is_idempotent_and_leaves_no_net_charge(connect, sim):
    async with connect("payments") as s:
        c = await call(s, "charge", pnr="MSLV42", amount="135.00", currency="USD",
                       idempotency_key="v-1")  # fmt: skip
        v = await call(s, "charge.void", charge_id=c["charge_id"])
        again = await call(s, "charge.void", charge_id=c["charge_id"])
        replay = await call(s, "charge", pnr="MSLV42", amount="135.00", currency="USD",
                            idempotency_key="v-1")  # fmt: skip
        unknown = await s.call_tool("charge.void", {"charge_id": "CH-NOPE"})
    assert v["status"] == "voided" and again["status"] == "voided"
    assert replay["status"] == "voided" and replay["charge_id"] == c["charge_id"]
    assert unknown.isError
    assert sim.partners.net_charged("MSLV42") == 0
