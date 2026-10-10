from acme.demo.supervisor import child_env, make_tokens


def test_tokens_unique_and_sales_matches():
    a, b = make_tokens(), make_tokens()
    assert a.sim_tokens != b.sim_tokens
    assert f"sales={a.sales}" in a.sim_tokens
    assert {"customer", "sales", "test-admin"} <= {p.split("=")[0] for p in a.sim_tokens.split(",")}


def test_child_env_defaults_fake_and_scopes_tokens():
    t = make_tokens()
    sim = child_env("sim", t, {})
    sales = child_env("sales", t, {})
    cust = child_env("customer", t, {})
    assert sim["ACME_SIM_TOKENS"] == t.sim_tokens
    assert sales["ACME_SALES_TOKEN"] == t.sales and "ACME_SIM_TOKENS" not in sales
    assert "ACME_SALES_TOKEN" not in cust and "ACME_SIM_TOKENS" not in cust
    assert cust["LLM_MODE"] == "fake"


def test_children_bind_loopback_only():
    t = make_tokens()
    assert child_env("customer", t, {})["ACME_CUSTOMER_HOST"] == "127.0.0.1"
    assert child_env("sales", t, {})["ACME_SALES_HOST"] == "127.0.0.1"
    assert child_env("sim", t, {})["ACME_SIM_HOST"] == "127.0.0.1"


def test_inherited_sim_tokens_are_not_reused():
    t = make_tokens()
    env = child_env("customer", t, {"ACME_SIM_TOKENS": "x=y", "LLM_MODE": "vertex"})
    assert "ACME_SIM_TOKENS" not in env and env["LLM_MODE"] == "vertex"


def test_tokens_repr_does_not_leak():
    t = make_tokens()
    assert t.sales not in repr(t)
