"""
Shared fixtures for the Ozone integration test suite.

These are LIVE integration tests — they talk to a real Ozone DEV environment.
Everything is driven by environment variables; any test whose required variables
are missing is skipped rather than failed, so a partial config still runs a useful
subset.

See tests/README.md for the full list of variables and how to run each part.
"""

import logging
import os

import pytest

from ozoneclient import OzoneClient

logger = logging.getLogger(__name__)

# Constants used by the negative / guard-rail cases. These are intentionally
# values that cannot exist in Ozone.
UNKNOWN_USERNAME = "definitely-not-a-real-user@example.invalid"
BAD_PASSWORD = "definitely-not-the-right-password-xyzzy"

# Core connection variables — without these nothing can run.
CORE_VARS = ("OZONE_URI", "OZONE_USERNAME", "OZONE_PASSWORD", "OZONE_APP_ID")


def _require(name):
    """Return the env var value or skip the requesting test if it is unset."""
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set; skipping test that requires it")
    return value


def mutating_enabled():
    """Part 2 (object-creating) tests only run when this is explicitly enabled."""
    return os.environ.get("OZONE_RUN_MUTATING", "").lower() in ("1", "true", "yes")


def so_id(created):
    return created.get("ServiceOrderId")

# --------------------------------------------------------------------------- #
# Connection / client
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def config():
    cfg = {name: None for name in CORE_VARS}
    for name in CORE_VARS:
        value = os.environ.get(name)
        if not value:
            pytest.skip(f"{name} not set; skipping live Ozone integration tests")
        cfg[name] = value
    cfg["OZONE_APP_NAME"] = os.environ.get("OZONE_APP_NAME", "ACX")
    cfg["OZONE_SCHEME"] = os.environ.get("OZONE_SCHEME", "http")
    return cfg


@pytest.fixture(scope="session")
def client(config):
    """A single authenticated client shared across the session."""
    return OzoneClient(
        username=config["OZONE_USERNAME"],
        password=config["OZONE_PASSWORD"],
        app_id=config["OZONE_APP_ID"],
        uri=config["OZONE_URI"],
        app_name=config["OZONE_APP_NAME"],
        scheme=config["OZONE_SCHEME"],
    )


@pytest.fixture
def no_write_guard(client, monkeypatch):
    """
    Spy that records (and still delegates) calls to the client's write helpers.

    Used by the Part 1 guard-rail tests to assert that a rejected request never
    reaches a POST/PATCH. Assert ``no_write_guard == []`` after the call.
    monkeypatch restores the originals automatically at the end of the test.
    """
    calls = []
    real_post = client._post
    real_patch = client._patch

    def spy_post(path, data):
        calls.append(("POST", path))
        return real_post(path, data)

    def spy_patch(path, data):
        calls.append(("PATCH", path))
        return real_patch(path, data)

    monkeypatch.setattr(client, "_post", spy_post)
    monkeypatch.setattr(client, "_patch", spy_patch)
    return calls


# --------------------------------------------------------------------------- #
# Test-data fixtures (each skips if its env var is missing)
# --------------------------------------------------------------------------- #
@pytest.fixture
def test_username():
    return _require("OZONE_TEST_USERNAME")


@pytest.fixture
def known_so_id():
    return _require("OZONE_KNOWN_SO_ID")


@pytest.fixture
def physical_so():
    return _require("OZONE_PHYSICAL_SO")


@pytest.fixture
def location():
    return _require("OZONE_LOCATION")


@pytest.fixture
def service_code():
    return _require("OZONE_SERVICE_CODE")


@pytest.fixture
def partyb_physical_so():
    return _require("OZONE_PARTYB_PHYSICAL_SO")


@pytest.fixture
def partya_vlanid():
    return _require("OZONE_PARTYA_VLANID")


@pytest.fixture
def partyb_vlanid():
    return _require("OZONE_PARTYB_VLANID")


@pytest.fixture
def partyb_account_guid():
    return _require("OZONE_PARTYB_ACCOUNT_GUID")


@pytest.fixture
def partyb_username():
    return _require("OZONE_PARTYB_USERNAME")


@pytest.fixture
def aws_vlanid():
    return _require("OZONE_AWS_VLANID")


@pytest.fixture
def aws_dxcon_id():
    return _require("OZONE_AWS_DXCON_ID")


@pytest.fixture
def aws_account_id():
    return _require("OZONE_AWS_ACCOUNT_ID")


@pytest.fixture
def azure_vlanid():
    return _require("OZONE_AZURE_VLANID")


@pytest.fixture
def azure_service_key():
    return _require("OZONE_AZURE_SERVICE_KEY")
