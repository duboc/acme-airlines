import pytest

import acme


def test_package_imports():
    assert acme.__version__


async def test_async_tests_run():
    assert True


@pytest.mark.live
def test_live_is_deselected_by_default():
    pytest.fail("live tests must not run unless selected with -m live")
