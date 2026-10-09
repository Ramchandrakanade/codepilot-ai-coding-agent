import pytest


@pytest.fixture(autouse=True)
def disable_rate_limits_for_regular_tests(monkeypatch):
    """Prevent rate limits from interfering with application validation tests."""
    import app.main as main

    monkeypatch.setattr(main.limiter, "enabled", False)
