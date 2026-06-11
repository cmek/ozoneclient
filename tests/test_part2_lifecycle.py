"""
Part 2 — Mutating lifecycle.

Anything that creates, activates, or cancels a service order. Creating Ozone
objects is EXPENSIVE, so the whole module is skipped unless explicitly enabled:

    OZONE_RUN_MUTATING=1 pytest tests/test_part2_lifecycle.py

Each test that creates an SO also cleans it up (best-effort cancellation) so DEV
does not accumulate orphans.

NOTE: the exact Ozone response schema is not pinned down. Where a field name is
unknown the test exercises the call and logs the response with a `TODO` marker so
the precise assertion can be filled in once a real response is observed.
"""

import logging

import pytest
from requests.exceptions import HTTPError

from ozoneclient import OzoneClientError

from conftest import mutating_enabled, so_id

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.mutating,
    pytest.mark.skipif(
        not mutating_enabled(),
        reason="set OZONE_RUN_MUTATING=1 to run object-creating tests",
    ),
]


def _cleanup_cancel(client, so, username):
    """Best-effort cancellation so a failed test does not leave an orphan SO."""
    try:
        client.cancel_service_order(so, "integration test cleanup", username)
    except Exception as e:  # noqa: BLE001 - cleanup must never mask the real failure
        logger.warning("cleanup cancellation of %s failed: %s", so, e)


# --------------------------------------------------------------------------- #
# P2-C / P2-D / P2-F / P2-L  — standard lifecycle on a single SO
# --------------------------------------------------------------------------- #
def test_p2_standard_lifecycle(
    client,
    test_username,
    physical_so,
    location,
    service_code,
    partyb_physical_so,
    partya_vlanid,
    partyb_vlanid,
):
    """
    P2-L1 (primary acceptance test), also covering P2-C1, P2-C4, P2-D1, P2-F1.

    create -> verify -> activate -> verify -> cancel -> verify, all on one SO,
    so we only ever create a single object.
    """
    name = f"acx-integration-test {partya_vlanid}/{partyb_vlanid}"

    # --- P2-C1: create -----------------------------------------------------
    created = client.create_service_order(
        physical_so, name, location, test_username, service_code
    )
    logger.info("create response: %s", created)
    so = so_id(created)
    assert so

    try:
        # --- P2-C4: create invalidated the cache, new SO visible without refresh
        orders = client.get_service_orders()
        assert isinstance(orders, list)
        # TODO: assert the new SO id appears in `orders` once the list schema is known

        # confirm the created SO reads back with the fields we sent
        fetched = client.get_service_order(so)
        logger.info("created SO: %s", fetched)
        # TODO: assert fetched Name/Location/ServiceCode/PhysicalSO match inputs

        # --- P2-D1: activate ----------------------------------------------
        activated = client.activate_service_order(
            so, partyb_physical_so, partya_vlanid, partyb_vlanid, test_username
        )
        logger.info("activate response: %s", activated)
        active_so = client.get_service_order(so)
        logger.info("activated SO: %s", active_so)
        # TODO: assert active_so reflects ACTIVE state and the correct
        #       ActivatedDateTime calendar date (seconds epoch)

        # --- P2-F1: cancel -------------------------------------------------
        cancelled = client.cancel_service_order(so, "test complete", test_username)
        logger.info("cancel response: %s", cancelled)
        cancelled_so = client.get_service_order(so)
        logger.info("cancelled SO: %s", cancelled_so)
        # TODO: assert cancelled_so reflects CANCELLED state and the correct
        #       LastBillingDate calendar date (seconds epoch)
    finally:
        _cleanup_cancel(client, so, test_username)


# --------------------------------------------------------------------------- #
# P2-E. AWS activation
# --------------------------------------------------------------------------- #
def test_p2_e1_aws_activation(
    client,
    test_username,
    physical_so,
    location,
    service_code,
    aws_vlanid,
    aws_dxcon_id,
    aws_account_id,
):
    created = client.create_service_order(
        physical_so, "acx-integration-test aws", location, test_username, service_code
    )
    so = so_id(created)
    try:
        activated = client.activate_aws_service_order(
            so, aws_vlanid, aws_dxcon_id, aws_account_id
        )
        logger.info("aws activate response: %s", activated)
        active_so = client.get_service_order(so)
        logger.info("aws activated SO: %s", active_so)
        # TODO: assert ACTIVE state + correct ActivatedDateTime date
    finally:
        _cleanup_cancel(client, so, test_username)


# --------------------------------------------------------------------------- #
# P2-EA. Azure ExpressRoute activation
# --------------------------------------------------------------------------- #
def test_p2_ea1_azure_activation(
    client,
    test_username,
    physical_so,
    location,
    service_code,
    azure_vlanid,
    azure_service_key,
):
    created = client.create_service_order(
        physical_so, "acx-integration-test azure", location, test_username, service_code
    )
    so = so_id(created)
    try:
        activated = client.activate_azure_service_order(
            so, azure_vlanid, azure_service_key
        )
        logger.info("azure activate response: %s", activated)
        active_so = client.get_service_order(so)
        logger.info("azure activated SO: %s", active_so)
        # TODO: assert ACTIVE state, correct ActivatedDateTime date, and that the
        #       ServiceKey was recorded on the SO
    finally:
        _cleanup_cancel(client, so, test_username)


# --------------------------------------------------------------------------- #
# Exploratory negatives — these issue writes that the API should reject.
# Opt-in separately (OZONE_RUN_NEGATIVE=1) because an unvalidated input could
# still create an object. They record behaviour rather than assert a fixed shape.
# --------------------------------------------------------------------------- #
def _negatives_enabled():
    import os

    return os.environ.get("OZONE_RUN_NEGATIVE", "").lower() in ("1", "true", "yes")


negatives = pytest.mark.skipif(
    not _negatives_enabled(),
    reason="set OZONE_RUN_NEGATIVE=1 to run exploratory write-rejection tests",
)


@negatives
def test_p2_e2_aws_invalid_ids(
    client, test_username, physical_so, location, service_code, aws_vlanid
):
    created = client.create_service_order(
        physical_so, "acx-integration-test aws-neg", location, test_username, service_code
    )
    so = so_id(created)
    try:
        with pytest.raises(HTTPError) as exc:
            client.activate_aws_service_order(so, aws_vlanid, "BAD-DXCON", "BAD-ACCT")
        logger.info("aws invalid-id response: %s", exc.value)
    finally:
        _cleanup_cancel(client, so, test_username)


@negatives
def test_p2_ea2_azure_invalid_service_key(
    client, test_username, physical_so, location, service_code, azure_vlanid
):
    created = client.create_service_order(
        physical_so,
        "acx-integration-test azure-neg",
        location,
        test_username,
        service_code,
    )
    so = so_id(created)
    try:
        with pytest.raises(HTTPError) as exc:
            client.activate_azure_service_order(so, azure_vlanid, "BAD-SERVICE-KEY")
        logger.info("azure invalid-key response: %s", exc.value)
    finally:
        _cleanup_cancel(client, so, test_username)


@negatives
def test_p2_d2_activate_nonexistent_so(
    client, test_username, partyb_physical_so, partya_vlanid, partyb_vlanid
):
    with pytest.raises((HTTPError, OzoneClientError)) as exc:
        client.activate_service_order(
            "SO-DOES-NOT-EXIST",
            partyb_physical_so,
            partya_vlanid,
            partyb_vlanid,
            test_username,
        )
    logger.info("activate non-existent SO response: %s", exc.value)


@negatives
def test_p2_f2_cancel_nonexistent_so(client, test_username):
    with pytest.raises(HTTPError) as exc:
        client.cancel_service_order("SO-DOES-NOT-EXIST", "neg test", test_username)
    logger.info("cancel non-existent SO response: %s", exc.value)
