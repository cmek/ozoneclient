#!/usr/bin/env python3
"""
Member-to-member / member-to-cloud circuit walkthrough against (staging) Ozone.

Drives the full lifecycle with the ozoneclient library, one clearly-labelled
step at a time:

    authenticate -> create SO -> read back -> activate -> read back
                 -> cancel -> read back

This is the SAME flow for a member-to-member circuit and for a "standard"-endpoint
cloud circuit (GCP / Huawei / Oracle). The only difference is who Party B is:
  - member-to-member : Party B is another member's account + physical SO
  - member-to-cloud  : Party B is the cloud provider's account + interconnect SO

It CREATES A REAL SERVICE ORDER in whatever Ozone you point it at. It pauses for
confirmation before each mutating call and cancels the SO at the end.

Usage:
    export OZONE_URI=staging-host[:port]
    export OZONE_USERNAME=...        # the service/app account
    export OZONE_PASSWORD=...
    export OZONE_APP_ID=...
    # then edit the CONFIG block below and run:
    python examples/circuit_walkthrough.py
"""

import os
import sys
import time

from ozoneclient import OzoneClient, OzoneClientError

# --------------------------------------------------------------------------- #
# CONFIG — connection (secrets from env, so they are not hard-coded here)
# --------------------------------------------------------------------------- #
OZONE_URI = os.environ.get("OZONE_URI", "staging-ozone.example")  # host[:port]
OZONE_USERNAME = os.environ.get("OZONE_USERNAME", "")
OZONE_PASSWORD = os.environ.get("OZONE_PASSWORD", "")
OZONE_APP_ID = os.environ.get("OZONE_APP_ID", "")
OZONE_APP_NAME = os.environ.get("OZONE_APP_NAME", "ACX")
OZONE_SCHEME = os.environ.get("OZONE_SCHEME", "https")

# --------------------------------------------------------------------------- #
# CONFIG — the scenario.  Fill these in for your staging environment.
# --------------------------------------------------------------------------- #

# Party A = the requesting member (the SO is created under this account).
PARTY_A_USERNAME = "requester@member-a.example"     # resolves to contact id + client guid
PARTY_A_PHYSICAL_SO = "SO000000"                    # Party A's physical cross-connect SO
LOCATION = "JB1"                                     # CT1 / CT2 / JB1
SERVICE_CODE = "VAX004"                              # virtual-circuit product code

# Party B = the far end (another member, or a cloud provider).
PARTY_B_PHYSICAL_SO = "SO111111"                    # Party B's physical SO
PARTY_B_ACCOUNT_GUID = "00000000-0000-0000-0000-000000000000"  # Party B's account RefGUID
PARTY_B_ACCEPTING_USERNAME = "approver@party-b.example"        # accepting contact

# Circuit details.
# For a cloud circuit prefix the description with the provider tag, e.g.
#   "[acx-oracle-tag]<ocid>", "[acx-huawei-tag]<id>", "[acx-gcp-tag]<pairing>"
DESCRIPTION = "acx-walkthrough-test"
PARTY_A_VLAN = 0          # 0 at activation for member-to-member (VLANs added later)
PARTY_B_VLAN = 0

# Safety: pause and ask before every write (create / activate / cancel).
CONFIRM_EACH_STEP = True


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def step(n, title):
    print(f"\n{'=' * 70}\nSTEP {n}: {title}\n{'=' * 70}")


def confirm(action):
    if not CONFIRM_EACH_STEP:
        return
    reply = input(f"  -> about to {action}. continue? [y/N] ").strip().lower()
    if reply not in ("y", "yes"):
        print("  aborted by user.")
        sys.exit(1)


def require_config():
    missing = [
        name
        for name, val in {
            "OZONE_URI": OZONE_URI,
            "OZONE_USERNAME": OZONE_USERNAME,
            "OZONE_PASSWORD": OZONE_PASSWORD,
            "OZONE_APP_ID": OZONE_APP_ID,
        }.items()
        if not val
    ]
    if missing:
        sys.exit(f"missing required env var(s): {', '.join(missing)}")


# --------------------------------------------------------------------------- #
# walkthrough
# --------------------------------------------------------------------------- #
def main():
    require_config()

    step(0, "authenticate")
    client = OzoneClient(
        username=OZONE_USERNAME,
        password=OZONE_PASSWORD,
        app_id=OZONE_APP_ID,
        uri=OZONE_URI,
        app_name=OZONE_APP_NAME,
        scheme=OZONE_SCHEME,
    )
    print(f"  authenticated: {client.is_authenticated}")

    step(1, "pre-flight lookups (no writes)")
    contact_id = client.get_contact_identifier_by_username(PARTY_A_USERNAME)
    client_guid = client.get_client_guid_by_username(PARTY_A_USERNAME)
    print(f"  Party A contact identifier : {contact_id}")
    print(f"  Party A client guid        : {client_guid}")
    if contact_id is None or client_guid is None:
        sys.exit("  Party A username does not resolve to a contact/client — fix config.")
    acc_id = client.get_contact_identifier_by_username(PARTY_B_ACCEPTING_USERNAME)
    print(f"  Party B accepting contact  : {acc_id}")
    if acc_id is None:
        sys.exit("  Party B accepting username does not resolve to a contact — fix config.")

    step(2, "create the virtual-circuit service order (Party A side)")
    confirm(f"CREATE an SO under {PARTY_A_USERNAME} against {PARTY_A_PHYSICAL_SO}")
    created = client.create_service_order(
        physical_so=PARTY_A_PHYSICAL_SO,
        name=DESCRIPTION,
        location=LOCATION,
        username=PARTY_A_USERNAME,
        service_code=SERVICE_CODE,
    )
    print(f"  create response: {created}")
    so = created.get("ServiceOrderId")
    if not so:
        sys.exit(f"  no ServiceOrderId in response: {created}")
    print(f"  >>> new service order: {so}")

    step(3, "read the new SO back")
    fetched = client.get_service_order(so)
    print(f"  Name             : {fetched.get('Name')}")
    print(f"  FinanceApproved  : {fetched.get('FinanceApproved')}  (expect False)")
    print(f"  Cancelled        : {fetched.get('Cancelled')}  (expect False)")

    step(4, "activate (attach Party B + Party B account explicitly)")
    confirm(f"ACTIVATE {so} with PartyB SO {PARTY_B_PHYSICAL_SO} / account {PARTY_B_ACCOUNT_GUID}")
    activated = client.activate_service_order(
        so=so,
        partyb_physical_so=PARTY_B_PHYSICAL_SO,
        partya_vlanid=PARTY_A_VLAN,
        partyb_vlanid=PARTY_B_VLAN,
        accepted_by_username=PARTY_B_ACCEPTING_USERNAME,
        # IMPORTANT: the SO belongs to Party A, so Party B's account can NOT be
        # derived from it — it must be passed explicitly.
        partyb_account_guid=PARTY_B_ACCOUNT_GUID,
    )
    print(f"  activate response: {activated}")

    step(5, "read back after activation")
    time.sleep(1)
    active_so = client.get_service_order(so)
    print(f"  FinanceApproved  : {active_so.get('FinanceApproved')}  (expect True)")

    step(6, "cancel (cleanup)")
    confirm(f"CANCEL {so}")
    cancelled = client.cancel_service_order(
        so=so,
        reason="acx walkthrough cleanup",
        username=PARTY_A_USERNAME,
    )
    print(f"  cancel response: {cancelled}")

    step(7, "read back after cancellation")
    time.sleep(1)
    cancelled_so = client.get_service_order(so)
    print(f"  CancellationRequest : {cancelled_so.get('CancellationRequest')}  (expect True)")

    print(f"\nDONE. Walked {so} through create -> activate -> cancel.")


if __name__ == "__main__":
    try:
        main()
    except OzoneClientError as e:
        sys.exit(f"\nOzone error: {e}")
    except KeyboardInterrupt:
        sys.exit("\ninterrupted.")
