# Member-to-Member virtual circuits — Ozone interaction

This document describes how a **member-to-member (M2M) virtual circuit** is
represented in Ozone (the CRM that is the source of truth for service orders) and
how the three lifecycle operations — **creation**, **activation** and
**cancellation** — map onto Ozone's REST API and onto `ozoneclient`.

It is written from the point of view of the ACX application, which drives Ozone,
but everything here is expressed in terms of the `OzoneClient` methods so it
doubles as a usage guide for the library.

## Actors

A member-to-member circuit connects two Teraco members across the exchange:

| Term | Meaning |
|------|---------|
| **Party A / requester** | The member who initiates (requests) the circuit. The virtual-circuit service order is created **under Party A's account**. |
| **Party B / acceptor / approver** | The member on the other end, who approves the request. Their physical port is attached at activation time. |
| **Physical SO** | A service order representing a member's physical cross-connect (port) into the exchange. Each party has its own. |
| **Virtual-circuit SO** | The service order representing the M2M circuit itself. Created in step 1, activated in step 2, cancelled in step 3. |

The single most important fact for using this API correctly:

> The virtual-circuit SO belongs to **Party A**. At activation we must tell Ozone
> **Party B's** account explicitly, because it cannot be derived from the SO.

See [Activation](#2-activation) and [The Party B account-guid
gotcha](#the-party-b-account-guid-gotcha).

## Lifecycle overview

```
  Party A requests                Party B approves                either party cancels
  ────────────────                ────────────────                ─────────────────────
  create_service_order  ───────►  activate_service_order  ──────►  cancel_service_order
  POST .../ServiceOrder/New/SO    PATCH .../ActivateBilling/       PATCH .../RequestCancellation/{so}
                                       Standard/{so}

  A-side PhysicalSO sent          B-side PartyB_PhysicalSO sent    LastBillingDate + reason sent
  account = Party A               PartyB_AccountRefGUID = Party B  cancelled-by = caller's contact
  contact = requester            (passed explicitly!)
```

VLANs are **not** configured here. At activation both `PartyA_VlanID` and
`PartyB_VlanID` are `0`; the real VLAN/VXLAN datapath is provisioned later by the
per-VLAN workflow, which is outside the scope of this document.

---

## 1. Creation

Party A requests the circuit. A new virtual-circuit service order is created in
Ozone **against Party A's own physical port and account**.

**Ozone:** `POST /rest/acxservice/v1/ServiceOrder/New/SO`

**Library:**

```python
from ozoneclient import OzoneClient

client = OzoneClient(
    username=service_account_username,
    password=service_account_password,
    app_id=app_id,
    uri=host,            # host[:port]; scheme defaults to "http"
)

created = client.create_service_order(
    physical_so=requester_physical_so,   # A-SIDE physical SO (Party A's port)
    name=description,                    # e.g. "[acx-vmware-tag]..." for tagged variants
    location=location_code,              # CT1 / CT2 / JB1
    username=requester_username,         # resolves to ContactIdentifier + Client RefGUID
    service_code="VAX004",               # virtual-circuit product code
)
service_order_id = created["ServiceOrderId"]
```

Fields sent to Ozone (`create_service_order` builds these):

| Ozone field | Source | Notes |
|-------------|--------|-------|
| `PhysicalSO` | `physical_so` | **Party A's** physical cross-connect SO |
| `Name` | `name` | free-text description / tags |
| `Location` | `location` | `CT1` / `CT2` / `JB1` |
| `ContactIdentifier` | derived from `username` | the requesting contact |
| `AccountRefGUID` | derived from `username` | the requester's client → **account = Party A** |
| `ServiceCode` | `service_code` | `VAX004` |

> **Note:** `create_service_order` derives `AccountRefGUID` from the creating
> user's contact (`get_client_guid_by_username`). The caller therefore only needs
> to know the requesting username, not the account GUID.

Only Party A's port is involved at this stage — Party B is unknown to Ozone until
activation.

---

## 2. Activation

Party B approves. Their physical port is attached and billing is activated.

**Ozone:** `PATCH /rest/acxservice/v1/ServiceOrder/ActivateBilling/Standard/{so}`

**Library:**

```python
activated = client.activate_service_order(
    so=service_order_id,                 # the virtual-circuit SO (created in step 1)
    partyb_physical_so=acceptor_physical_so,   # B-SIDE physical SO (Party B's port)
    partya_vlanid=0,                     # 0 at activation; VLANs added later
    partyb_vlanid=0,
    accepted_by_username=approver_username,    # the approving (Party B) user
    partyb_account_guid=party_b_account_guid,  # REQUIRED for M2M — Party B's account RefGUID
)
```

Fields sent to Ozone:

| Ozone field | Source | Notes |
|-------------|--------|-------|
| `ActivatedDateTime` | `int(time())` | seconds epoch, set by the client |
| `PartyB_PhysicalSO` | `partyb_physical_so` | **Party B's** physical cross-connect SO |
| `PartyB_AccountRefGUID` | `partyb_account_guid` | **Party B's** account — see gotcha below |
| `PartyA_VlanID` | `partya_vlanid` | `0` at activation |
| `PartyB_VlanID` | `partyb_vlanid` | `0` at activation |
| `AcceptedBy_ContactIdentifier` | derived from `accepted_by_username` | the approving contact |

### The Party B account-guid gotcha

`activate_service_order` can resolve `PartyB_AccountRefGUID` in two ways:

- **Cloud activations (AWS / Azure / standard customer circuits):** the SO being
  activated *is* the customer's (Party B's) order, so the account guid is derived
  from the SO automatically — `partyb_account_guid` may be omitted.
- **Member-to-member:** the SO being activated is the **virtual-circuit SO that
  Party A created**, so its account is **Party A**. Deriving the guid from the SO
  would send Party A's account as `PartyB_AccountRefGUID` — wrong. You **must**
  pass `partyb_account_guid` explicitly; when supplied it takes precedence and the
  SO-derivation step is skipped.

```python
# WRONG for M2M — would send Party A's account as PartyB_AccountRefGUID
client.activate_service_order(so, partyb_physical_so, 0, 0, approver_username)

# CORRECT for M2M — Party B's account is explicit
client.activate_service_order(
    so, partyb_physical_so, 0, 0, approver_username,
    partyb_account_guid=party_b_account_guid,
)
```

After activation, re-read the SO (`client.get_service_order(so)`) to refresh any
local mirror of the Ozone state.

---

## 3. Cancellation

Either party can request cancellation of the circuit's service order.

**Ozone:** `PATCH /rest/acxservice/v1/ServiceOrder/RequestCancellation/{so}`

**Library:**

```python
cancelled = client.cancel_service_order(
    so=service_order_id,
    reason=cancellation_reason,   # free text
    username=requesting_username,  # resolves to the cancelling ContactIdentifier
)
```

Fields sent to Ozone:

| Ozone field | Source | Notes |
|-------------|--------|-------|
| `LastBillingDate` | `int(time())` | seconds epoch, set by the client |
| `CancellationReason` | `reason` | free text |
| `ContactIdentifier` | derived from `username` | the contact requesting cancellation |

This issues a **cancellation request**; Ozone (finance) processes the actual
cancellation/expiry. The subsequent state is picked up the next time the SO is
read or the periodic sync runs.

---

## Which physical port is sent when — quick reference

| Operation | Field | Which side |
|-----------|-------|------------|
| Create | `PhysicalSO` | **Party A** (requester) physical SO |
| Activate | `PartyB_PhysicalSO` | **Party B** (acceptor) physical SO |

Party A's port is bound to the SO at creation and is **not** re-sent at
activation; only Party B's port is added then.

## Caching note

`OzoneClient` caches the big list endpoints (`get_service_orders`,
`get_clients`, `get_contacts`). All three write operations above
(`create_service_order`, `activate_service_order`, `cancel_service_order`)
invalidate the `service_orders` cache automatically, so a subsequent
`get_service_orders()` reflects the change without an explicit `refresh=True`.
Reads of a single SO (`get_service_order`) are always uncached.
