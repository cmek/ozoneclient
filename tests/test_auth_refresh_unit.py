"""
Offline unit tests for token refresh in OzoneClient.

Unlike the Part 1/Part 2 suites these need no live Ozone server and no
environment variables: the bootstrap Authenticate call and the session are
monkeypatched. They pin down the long-running-process behaviour that bit the
IdP: a client built once must keep working after its token expires locally
(pre-emptive refresh) and after the server invalidates the token early
(401 -> re-auth -> retry once).

Run:  pytest tests/test_auth_refresh_unit.py
"""

import json
from time import time

import pytest

import ozoneclient.oc as oc
from ozoneclient import OzoneClient, OzoneClientError


def _expires_at(seconds_from_now):
    # Ozone reports ExpiresAt in milliseconds
    return int((time() + seconds_from_now) * 1000)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise oc.requests.HTTPError(
                f"{self.status_code} Client Error", response=self
            )


class FakeOzone:
    """Stands in for both the Authenticate endpoint and the session."""

    def __init__(self, token_ttl_seconds=3600):
        self.token_ttl_seconds = token_ttl_seconds
        self.auth_calls = 0
        self.requests = []  # (method, url, Authorization header)
        # queue of responses for session requests; empty -> 200
        self.responses = []

    def current_token(self):
        return f"tok-{self.auth_calls}"

    def authenticate(self, url, **kwargs):
        self.auth_calls += 1
        return FakeResponse(
            200,
            {
                "AccessToken": self.current_token(),
                "ExpiresAt": _expires_at(self.token_ttl_seconds),
            },
        )

    def bind(self, client):
        def session_request(method, url, **kwargs):
            self.requests.append(
                (method, url, client.s.headers.get("Authorization"))
            )
            if self.responses:
                return self.responses.pop(0)
            return FakeResponse(200)

        client.s.request = session_request


@pytest.fixture
def fake(monkeypatch):
    fake = FakeOzone()
    monkeypatch.setattr(oc.requests, "post", fake.authenticate)
    return fake


@pytest.fixture
def client(fake):
    client = OzoneClient(
        username="svc-user",
        password="svc-pass",
        app_id="app-id",
        uri="ozone.example:8443",
    )
    fake.bind(client)
    return client


class TestTokenRefresh:
    def test_valid_token_is_reused(self, client, fake):
        client._get("some/path")
        client._get("some/path")
        assert fake.auth_calls == 1
        assert [r[2] for r in fake.requests] == ["tok-1", "tok-1"]

    def test_expired_token_refreshes_before_request(self, client, fake):
        # simulate the token passing its expiry while the process is running
        client.token["ExpiresAt"] = 0
        client._post("some/path", {"a": 1})
        assert fake.auth_calls == 2
        # the request went out with the fresh token, not the stale one
        assert fake.requests[-1][2] == "tok-2"

    def test_server_side_invalidation_retries_once(self, client, fake):
        # token still valid locally, but the server rejects it
        fake.responses = [FakeResponse(401), FakeResponse(200)]
        r = client._post("some/path", {"a": 1})
        assert r.status_code == 200
        assert fake.auth_calls == 2
        # first attempt with the old token, retry with the fresh one
        assert [r[2] for r in fake.requests] == ["tok-1", "tok-2"]

    def test_401_after_fresh_auth_is_not_retried(self, client, fake):
        # expired token forces a refresh; the 401 that follows is then about
        # the request itself (e.g. bad user credentials) and must not trigger
        # another auth round-trip
        client.token["ExpiresAt"] = 0
        fake.responses = [FakeResponse(401)]
        with pytest.raises(OzoneClientError) as excinfo:
            client._post("some/path", {"a": 1})
        assert excinfo.value.status_code == 401
        assert fake.auth_calls == 2  # constructor + pre-request refresh only
        assert len(fake.requests) == 1

    def test_persistent_401_raises_after_single_retry(self, client, fake):
        fake.responses = [FakeResponse(401), FakeResponse(401)]
        with pytest.raises(OzoneClientError) as excinfo:
            client._post("some/path", {"a": 1})
        assert excinfo.value.status_code == 401
        assert len(fake.requests) == 2


class TestVerifyUser:
    def test_verify_user_false_on_401(self, client, fake):
        client.token["ExpiresAt"] = 0
        fake.responses = [FakeResponse(401)]
        assert client.verify_user("dave@ptxtech.io", "wrong") is False

    def test_verify_user_true_after_stale_token_retry(self, client, fake):
        # the exact IdP failure mode: correct user credentials, stale service
        # token -> must recover transparently instead of returning False
        fake.responses = [FakeResponse(401), FakeResponse(200)]
        assert client.verify_user("dave@ptxtech.io", "right") is True
