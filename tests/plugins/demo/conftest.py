# The demo tests' shared fixtures: the shipped profile flown once, and the
# real database with the generator replaced by that flight.

import pytest

import fixgw.database as database
import fixgw.plugins.demo as demo


@pytest.fixture(scope="module")
def flown():
    pytest.importorskip("geographiclib")
    from fixgw.sim import generator

    return generator.generate(demo.Plugin._profile_path(demo.DEFAULT_PROFILE))


@pytest.fixture
def db(flown, monkeypatch):
    from fixgw.sim import generator

    database.init("src/fixgw/config/database.yaml")
    database._stop_update_thread()
    monkeypatch.setattr(generator, "generate", lambda *a, **k: flown)
    yield
    database._stop_update_thread()
