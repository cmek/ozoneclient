"""
Part 1 — Non-mutating validation.

Reads, authentication, and the client-side guard rails that reject bad input
*before* any service-order write. These create/modify NOTHING in Ozone, so they
are cheap and safe to run frequently.

Run:  pytest tests/test_part1_validation.py
"""

import os

import pytest
from requests.exceptions import HTTPError

from ozoneclient import OzoneClient, OzoneClientError

from conftest import BAD_PASSWORD, UNKNOWN_USERNAME


# --------------------------------------------------------------------------- #
# P1-A. Connectivity & authentication
# --------------------------------------------------------------------------- #
class TestAuth:
    def test_p1_a1_construct_valid(self, client):
        assert client.is_authenticated is True
        assert client.token.get("AccessToken")
        assert client.token.get("ExpiresAt")

    def test_p1_a2_construct_bad_password(self, config):
        with pytest.raises(HTTPError):
            OzoneClient(
                username=config["OZONE_USERNAME"],
                password=BAD_PASSWORD,
                app_id=config["OZONE_APP_ID"],
                uri=config["OZONE_URI"],
                app_name=config["OZONE_APP_NAME"],
                scheme=config["OZONE_SCHEME"],
            )

    def test_p1_a3_verify_user_valid(self, client, config):
        assert (
            client.verify_user(config["OZONE_TEST_USERNAME"],
                               config["OZONE_TEST_PASSWORD"])
            is True
        )

    def test_p1_a4_verify_user_bad_password(self, client, config):
        assert client.verify_user(config["OZONE_TEST_USERNAME"], BAD_PASSWORD) is False

    def test_p1_a5_token_expiry_reauth(self, client):
        # force the token into the expiry window
        client.token["ExpiresAt"] = 0
        assert client.is_authenticated is False
        # a read transparently re-authenticates
        client.get_clients(refresh=True)
        assert client.is_authenticated is True


# --------------------------------------------------------------------------- #
# P1-B. Lookups
# --------------------------------------------------------------------------- #
class TestLookups:
    def test_p1_b1_contact_identifier(self, client, test_username):
        assert client.get_contact_identifier_by_username(test_username) is not None

    def test_p1_b2_client_guid(self, client, test_username):
        assert client.get_client_guid_by_username(test_username) is not None

    def test_p1_b3_unknown_username(self, client):
        assert client.get_contact_identifier_by_username(UNKNOWN_USERNAME) is None
        assert client.get_client_guid_by_username(UNKNOWN_USERNAME) is None

    def test_p1_b4_cache_then_refresh(self, client):
        first = client.get_contacts()
        second = client.get_contacts()
        # same cached object is returned on the second call
        assert first is second
        # refresh forces a fresh pull -> a different object
        refreshed = client.get_contacts(refresh=True)
        assert refreshed is not first

    def test_p1_b5_get_service_order(self, client, known_so_id):
        so = client.get_service_order(known_so_id)
        assert so  # uncached read of a pre-existing SO


# --------------------------------------------------------------------------- #
# P1-G. Client-side guard rails (must raise BEFORE any write)
# --------------------------------------------------------------------------- #
class TestGuardRails:
    def test_p1_g1_create_unknown_username(self, client, no_write_guard):
        with pytest.raises(OzoneClientError):
            client.create_service_order(
                "PSO-DUMMY", "guard-rail test", "CT1", UNKNOWN_USERNAME, "VAX004"
            )
        assert no_write_guard == []  # no POST was issued

    def test_p1_g2_create_username_without_client_guid(self, client, no_write_guard):
        username = os.environ.get("OZONE_USERNAME_NO_CLIENT")
        if not username:
            pytest.skip("OZONE_USERNAME_NO_CLIENT not set")
        with pytest.raises(OzoneClientError):
            client.create_service_order(
                "PSO-DUMMY", "guard-rail test", "CT1", username, "VAX004"
            )
        assert no_write_guard == []

    def test_p1_g3_activate_unknown_username(self, client, no_write_guard):
        with pytest.raises(OzoneClientError):
            client.activate_service_order(
                "SO-DUMMY", "PSO-DUMMY", "100", "200", UNKNOWN_USERNAME
            )
        assert no_write_guard == []  # no PATCH was issued

    def test_p1_g4_activate_so_without_account_guid(
        self, client, no_write_guard, test_username
    ):
        so = os.environ.get("OZONE_SO_NO_ACCOUNT")
        if not so:
            pytest.skip("OZONE_SO_NO_ACCOUNT not set")
        with pytest.raises(OzoneClientError):
            client.activate_service_order(so, "PSO-DUMMY", "100", "200", test_username)
        # only a GET (to fetch the SO) is allowed; no PATCH
        assert no_write_guard == []

    def test_p1_g5_cancel_unknown_username(self, client, no_write_guard):
        with pytest.raises(OzoneClientError):
            client.cancel_service_order("SO-DUMMY", "guard-rail test", UNKNOWN_USERNAME)
        assert no_write_guard == []  # no PATCH was issued


# --------------------------------------------------------------------------- #
# P1-H. Member-to-member activation guid resolution
#
# These exercise the branch logic of activate_service_order's partyb_account_guid
# argument without creating anything: the contact lookup and the PATCH itself are
# stubbed, so we assert purely on which guid ends up in the request body and
# whether the SO-derivation path is taken. (The live activation is covered by the
# Part 2 lifecycle test.)
# --------------------------------------------------------------------------- #
class _FakeResponse:
    @staticmethod
    def json():
        return {}


class TestMemberActivationGuid:
    def test_p1_h1_explicit_guid_is_sent_and_skips_so_derivation(
        self, client, monkeypatch
    ):
        """
        member-to-member: the activated SO belongs to Party A, so an explicit
        partyb_account_guid must be sent verbatim and the SO-account lookup must
        be skipped entirely.
        """
        monkeypatch.setattr(
            client, "get_contact_identifier_by_username", lambda u: "CID-FAKE"
        )

        def _must_not_derive(so):
            raise AssertionError(
                "get_service_order_account_guid must not be called when "
                "partyb_account_guid is supplied"
            )

        monkeypatch.setattr(client, "get_service_order_account_guid", _must_not_derive)

        captured = {}

        def fake_patch(path, data):
            captured["path"] = path
            captured["data"] = data
            return _FakeResponse()

        monkeypatch.setattr(client, "_patch", fake_patch)

        client.activate_service_order(
            "VC-SO-PARTY-A",
            "PARTYB-PSO",
            0,
            0,
            "approver@example.com",
            partyb_account_guid="PARTYB-GUID",
        )

        assert captured["data"]["PartyB_AccountRefGUID"] == "PARTYB-GUID"
        assert captured["data"]["PartyB_PhysicalSO"] == "PARTYB-PSO"
        assert captured["data"]["AcceptedBy_ContactIdentifier"] == "CID-FAKE"
        assert "ActivateBilling/Standard/VC-SO-PARTY-A" in captured["path"]

    def test_p1_h2_without_explicit_guid_derives_from_so(self, client, monkeypatch):
        """
        cloud behaviour is preserved: with no override the guid is derived from
        the activated SO's account.
        """
        monkeypatch.setattr(
            client, "get_contact_identifier_by_username", lambda u: "CID-FAKE"
        )

        derived_for = []

        def fake_derive(so):
            derived_for.append(so)
            return "DERIVED-GUID"

        monkeypatch.setattr(client, "get_service_order_account_guid", fake_derive)

        captured = {}

        def fake_patch(path, data):
            captured["data"] = data
            return _FakeResponse()

        monkeypatch.setattr(client, "_patch", fake_patch)

        client.activate_service_order("SO1", "PSO", 0, 0, "approver@example.com")

        assert derived_for == ["SO1"]  # derivation path was taken
        assert captured["data"]["PartyB_AccountRefGUID"] == "DERIVED-GUID"
