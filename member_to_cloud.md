# Member-to-cloud virtual circuits — Ozone interaction

This document describes how a **member-to-cloud virtual circuit** — a circuit
between a Teraco member and a cloud provider that uses the **standard** service
order endpoints (currently **GCP, Huawei and Oracle**) — is represented in Ozone
and driven with `ozoneclient`.

It is a companion to [`member_to_member.md`](./member_to_member.md). Read that
first: member-to-cloud reuses the **exact same** Ozone machinery, so only the
differences are called out here.

> **Scope.** This covers cloud providers that activate through the *Standard*
> billing endpoint (`ActivateBilling/Standard/{so}`). **AWS and Azure are NOT
> covered here** — they use dedicated activation endpoints
> (`activate_aws_service_order` / `activate_azure_service_order`) and do not take
> a Party B account at all.

## The key idea: cloud VCs are member-to-member circuits

There is no separate Ozone object for a cloud circuit. A member-to-cloud VC is a
`"Standard"` circuit-type service order, product code `VAX004` — identical to a
member-to-member circuit. It is created with
[`create_service_order`](#1-creation) and activated with
[`activate_service_order`](#2-activation), the same two calls.

The cloud provider simply plays the role of **Party B**. What tells a cloud VC
apart from a plain member VC afterwards is a **tag** in the description — see
[Tagging](#tagging).

## Actors

| Term | Member-to-cloud meaning |
|------|--------------------------|
| **Party A / requester** | The **Teraco member (customer)**. The virtual-circuit SO is created under the customer's account, against the customer's physical port. |
| **Party B / acceptor** | The **cloud provider** side. Its physical port and account are attached at activation. |
| **Customer physical SO** | The service order for the member's physical cross-connect (port). This is Party A's endpoint. |
| **Provider physical SO** | The service order for the provider-side interconnect port. This is Party B's endpoint. |

Which physical port is sent when — same rule as member-to-member:

| Operation | Field | Which side |
|-----------|-------|------------|
| Create | `PhysicalSO` | **Party A** — the customer's physical SO |
| Activate | `PartyB_PhysicalSO` | **Party B** — the cloud provider's physical SO |

---

## 1. Creation

The customer's request creates the virtual-circuit SO under the **customer's**
account, against the **customer's** physical port. The provider is not yet
involved.

```python
from ozoneclient import OzoneClient

client = OzoneClient(username=..., password=..., app_id=..., uri=...)

created = client.create_service_order(
    physical_so=customer_physical_so,   # PARTY A — customer's physical SO
    name=f"[acx-oracle-tag]{provider_circuit_id}",  # tag + provider identifier
    location=location_code,             # CT1 / CT2 / JB1
    username=requester_username,        # the customer's requesting user
    service_code="VAX004",
)
service_order_id = created["ServiceOrderId"]
```

`create_service_order` derives `ContactIdentifier` and `AccountRefGUID` from
`username`, so the SO's **account is the customer (Party A)**. Remember this — it
is the whole reason activation needs an explicit account (next section).

---

## 2. Activation

When the circuit is provisioned, the **provider** side is attached.

```python
client.activate_service_order(
    so=service_order_id,                 # the virtual-circuit SO (account = Party A / customer)
    partyb_physical_so=provider_physical_so,    # PARTY B — the cloud provider's physical SO
    partya_vlanid=vlan,
    partyb_vlanid=vlan,
    accepted_by_username=provider_accepting_username,  # provider-side accepting contact
    partyb_account_guid=provider_account_guid,   # PARTY B account — REQUIRED, see below
)
```

### Which account to use for activation

This is the single most important rule for member-to-cloud, and the easiest to
get wrong:

> **You MUST pass `partyb_account_guid` explicitly, and it must be the cloud
> provider's account RefGUID (Party B) — never the customer's.**

Why this is mandatory here:

- `activate_service_order` will, *when `partyb_account_guid` is omitted*, derive
  the account from the SO being activated (the cloud convenience for AWS/Azure-style
  flows where the activated SO already belongs to the customer side).
- For member-to-cloud the activated SO was created under the **customer's**
  account (Party A). Deriving from it would set `PartyB_AccountRefGUID` to the
  **customer**, which is wrong — Party B is the provider.
- Therefore the provider's account guid must be supplied by the caller.

```python
# WRONG — omitting partyb_account_guid sends the customer (Party A) as Party B
client.activate_service_order(so, provider_physical_so, vlan, vlan, accepting_username)

# CORRECT — Party B account is the cloud provider, passed explicitly
client.activate_service_order(
    so, provider_physical_so, vlan, vlan, accepting_username,
    partyb_account_guid=provider_account_guid,
)
```

Where the provider account guid comes from:

- **Oracle / Huawei** — the provider's client RefGUID (the `b_party_key` for that
  provider's Ozone account).
- **GCP** — the account that owns the GCP-side interconnect (the GCP/Teraco
  interconnect account), paired with the GCP interconnect's physical SO as
  `partyb_physical_so`.

The accepting contact (`accepted_by_username` → `AcceptedBy_ContactIdentifier`)
is a contact under the **provider's** account (e.g. an `InterconnectRequester`
role), not the customer.

---

## 3. Cancellation

Cancellation is identical to member-to-member — `cancel_service_order` on the
virtual-circuit SO. There is nothing cloud-specific about it.

```python
client.cancel_service_order(
    so=service_order_id,
    reason=cancellation_reason,
    username=requesting_username,
)
```

---

## Tagging

All standard-endpoint cloud VCs share the `"Standard"` circuit type, so the
**description prefix is what classifies them**. At creation, set `name` to
`<tag><provider-identifier>`. The tag round-trips into Ozone as the circuit's
`CircuitName` and is later used to (a) exclude these circuits from generic
member-to-member processing and (b) select them in provider-specific views.

| Provider | Tag | Provider identifier appended | Activation endpoint |
|----------|-----|------------------------------|---------------------|
| Oracle | `[acx-oracle-tag]` | Oracle circuit OCID | Standard |
| Huawei | `[acx-huawei-tag]` | hosted-connection id | Standard |
| GCP | `[acx-gcp-tag]` | pairing name | Standard |
| VMware | `[acx-vmware-tag]` | (prefixed onto the user-supplied description) | Standard |
| AWS | — | — | dedicated (`ActivateBilling/AWS`) |
| Azure | — | — | dedicated (`ActivateBilling/MSXR`) |

Notes:

- Keep the tag spelling **exactly** consistent between the writer (the `name`
  passed to `create_service_order`) and every reader that filters on
  `CircuitName`. A mismatch silently breaks classification.
- The tag must be part of `name` at **create** time; it is not a separate Ozone
  field.

---

## Field reference

What ends up on the Ozone request for the two write calls (cloud values shown):

**`create_service_order` → `POST /ServiceOrder/New/SO`**

| Ozone field | Value | Side |
|-------------|-------|------|
| `PhysicalSO` | customer physical SO | Party A |
| `Name` | `<tag><provider-id>` | — |
| `Location` | `CT1` / `CT2` / `JB1` | — |
| `ContactIdentifier` | derived from `username` | customer contact |
| `AccountRefGUID` | derived from `username` | **account = Party A (customer)** |
| `ServiceCode` | `VAX004` | — |

**`activate_service_order` → `PATCH /ServiceOrder/ActivateBilling/Standard/{so}`**

| Ozone field | Value | Side |
|-------------|-------|------|
| `ActivatedDateTime` | `int(time())` (set by client) | — |
| `PartyB_PhysicalSO` | provider physical SO | Party B |
| `PartyB_AccountRefGUID` | **provider account guid (explicit)** | Party B |
| `PartyA_VlanID` / `PartyB_VlanID` | the VLAN(s) | — |
| `AcceptedBy_ContactIdentifier` | derived from `accepted_by_username` | provider contact |
