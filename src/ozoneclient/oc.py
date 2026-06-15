import logging
import json
from urllib3.util import Retry
from requests.adapters import HTTPAdapter
from requests.exceptions import HTTPError
import requests
from time import time

logger = logging.getLogger(__name__)

RETRIES_TOTAL = 3
RETRIES_BACKOFF_FACTOR = 2
RETRIES_STATUS_FORCELIST = [502, 503, 504]


class OzoneClientError(Exception):
    pass


class OzoneClient(object):
    # defaults to DEV environment
    def __init__(
        self,
        username,
        password,
        app_id,
        uri,
        app_name="ACX",
        timeout_seconds=15,
        scheme="http",
    ):
        self.token = None
        # instance-level cache for the big list endpoints, keyed by name.
        # populated lazily and reused until an explicit refresh is requested.
        self._cache = {}
        # requests session parameter
        self.s = requests.Session()
        # retries class
        retries = Retry(
            total=RETRIES_TOTAL,
            backoff_factor=RETRIES_BACKOFF_FACTOR,
            status_forcelist=RETRIES_STATUS_FORCELIST,
            allowed_methods={"GET"},
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.s.mount("http://", adapter)
        self.s.mount("https://", adapter)

        self.username = username
        self.password = password
        self.app_id = app_id
        self.app_name = app_name
        self.uri = uri
        self.scheme = scheme

        self.timeout_seconds = timeout_seconds
        self._auth()

    def _url(self, path):
        return f"{self.scheme}://{self.uri}/{path}"

    def invalidate_cache(self, *keys):
        """
        drops cached list responses. with no args clears everything; otherwise
        clears only the named keys (e.g. "service_orders", "clients",
        "contacts"). called internally after writes that make a list stale.
        """
        if not keys:
            self._cache.clear()
            return
        for key in keys:
            self._cache.pop(key, None)

    @property
    def is_authenticated(self):
        if self.token is None:
            return False

        # consider token expired if the expiry timestamp is
        # within one minute from now
        expiry_date = int(self.token.get("ExpiresAt", 0) / 1000)
        now = int(time())
        if int(time()) >= expiry_date - 60:
            logger.info(f"the auth token has expired {expiry_date} <= {now}...")
            return False

        return True

    def _auth(self):
        if self.is_authenticated:
            logger.info("we're already authenticated...")
            return

        url = self._url("rest/teracoczauthservice/v1/Authenticate")
        logger.info(f"trying to authenticate with ozone on {url}")
        # the bootstrap auth call is issued without the session so it never
        # carries a stale Authorization header from a previous (expired) token
        r = requests.post(
            url,
            data=json.dumps(
                {
                    "Username": self.username,
                    "Password": self.password,
                }
            ),
            headers={
                "Content-type": "application/json",
                "ApplicationName": self.app_name,
                "ApplicationID": self.app_id,
            },
            timeout=self.timeout_seconds,
        )

        r.raise_for_status()
        logger.info("authenticated successfully...")
        self.token = r.json()
        self.s.headers.update(
            {
                "Authorization": self.token.get("AccessToken"),
                "Content-type": "application/json",
            }
        )

    def _get(self, path):
        self._auth()
        url = self._url(path)
        logger.info(f"sending authenticated GET request to: {url}")
        try:
            r = self.s.get(url, timeout=self.timeout_seconds)
            r.raise_for_status()
        except requests.HTTPError as e:
            logger.error(
                f"GET {url} failed with status {e.response.status_code}: {e.response.json()}"
            )
            raise OzoneClientError(
                f"GET request failed: {e.response.status_code} - {e.response.json()}"
            ) from e
        return r

    def _post(self, path, data):
        self._auth()
        url = self._url(path)
        logger.info(f"sending authenticated POST request to: {url}")
        try:
            r = self.s.post(url, data=json.dumps(data), timeout=self.timeout_seconds)
            r.raise_for_status()
        except requests.HTTPError as e:
            logger.error(
                f"POST {url} failed with status {e.response.status_code}: {e.response.json()}"
            )
            raise OzoneClientError(
                f"POST request failed: {e.response.status_code} - {e.response.json()}"
            ) from e
        return r

    def _patch(self, path, data):
        self._auth()
        url = self._url(path)
        logger.info(f"sending authenticated PATCH request to: {url}")
        try:
            r = self.s.patch(url, data=json.dumps(data), timeout=self.timeout_seconds)
            logger.debug(f"PATCH {url} responded with: {r.text}")
            r.raise_for_status()
        except requests.HTTPError as e:
            logger.error(
                f"PATCH {url} failed with status {e.response.status_code}: {e.response.json()}"
            )
            raise OzoneClientError(
                f"PATCH request failed: {e.response.status_code} - {e.response.json()}"
            ) from e
        return r

    def verify_user(self, username, password):
        logger.info(f"verifying user {username}")

        try:
            self._post(
                "rest/teracoczauthservice/v1/VerifyUser",
                {"Username": username, "Password": password},
            )
        except HTTPError as e:
            logger.info(
                f"failed to authenticate user {username}, return code: {e.response.status_code}, error: {e}"
            )
            return False
        except Exception as e:
            logger.warning(
                f"non standard exception detected while handling verification for user {username}: {e}"
            )
            return False

        return True

    def get_contacts(self, refresh=False):
        """
        returns all contacts. cached by default; pass refresh=True to force a
        fresh pull and update the cache.
        """
        if refresh or "contacts" not in self._cache:
            logger.info("getting all contacts")
            self._cache["contacts"] = self._get("rest/acxservice/v1/Contact").json()
        return self._cache["contacts"]

    def get_contact(self, uid):
        logger.info(f"getting contact info for {uid}")
        r = self._get(f"rest/acxservice/v1/Contact/{uid}")
        return r.json()

    def get_contact_by_username(self, username):
        logger.info(f"getting contact info for {username}")
        # return the first contact whose account username matches, or None
        return next(
            (
                contact
                for contact in self.get_contacts()
                if contact.get("Account", {}).get("Username") == username
            ),
            None,
        )

    def get_contact_identifier_by_username(self, username):
        """
        finds the contact by username and returns the value of its contact identifier part
        """
        contact = self.get_contact_by_username(username)
        if not contact:
            return None
        return contact.get("ContactIdentifier", None)

    def get_client_guid_by_username(self, username):
        """
        finds the contact by username and returns the value of its ClientRegGUID
        """
        contact = self.get_contact_by_username(username)
        if not contact:
            return None
        return contact.get("Client", {}).get("RefGUID", None)

    def get_contacts_by_client(self, guid):
        """
        extracts contact info by client guid
        """
        logger.info(f"getting contacts for client identified by {guid}")
        r = self._get(f"rest/acxservice/v1/Contact/byClient/{guid}")
        return r.json()

    def get_clients(self, refresh=False):
        """
        returns all clients. cached by default; pass refresh=True to force a
        fresh pull and update the cache.
        """
        if refresh or "clients" not in self._cache:
            logger.info("getting all clients")
            self._cache["clients"] = self._get("rest/acxservice/v1/Client").json()
        return self._cache["clients"]

    def get_client(self, guid):
        logger.info(f"getting client by refguid {guid}")
        r = self._get(f"rest/acxservice/v1/Client/{guid}")
        return r.json()

    def get_service_orders(self, refresh=False):
        """
        returns all service orders. cached by default; pass refresh=True to
        force a fresh pull and update the cache.
        """
        if refresh or "service_orders" not in self._cache:
            logger.info("getting all service orders...")
            self._cache["service_orders"] = self._get(
                "rest/acxservice/v1/ServiceOrder"
            ).json()
        return self._cache["service_orders"]

    def get_service_order(self, so):
        """
        return service order by id (ServiceOrderId)
        """
        logger.info(f"getting service order: {so}")
        r = self._get(f"rest/acxservice/v1/ServiceOrder/{so}")
        return r.json()

    def create_service_order(self, physical_so, name, location, username, service_code):
        """
        creates a new Service Order
        physical_so - service order representing the physical port
        name - just description, uses tags like [acx-vmware-tag] for cloud providers other than aws and azure
        location - (get this from physicalConnectionservice.location.code). This is currently one of these three: CT1, CT2, JB1
        username - username (the one that looks like email) of the Contact creating this SO
        service_code VAX004 for virtual circuits
        po_number ?? - this is present in the postman collection but looks like it's not required?
        """

        contact_identifier = self.get_contact_identifier_by_username(username)

        if contact_identifier is None:
            raise OzoneClientError(
                f"contact identifier not found for username {username}"
            )

        client_guid = self.get_client_guid_by_username(username)
        if client_guid is None:
            raise OzoneClientError(f"client_guid not found for username {username}")

        # XXX TODO - validate physical_so

        data = {
            "PhysicalSO": physical_so,
            "Name": name,
            "Location": location,
            "ContactIdentifier": contact_identifier,
            "AccountRefGUID": client_guid,
            "ServiceCode": service_code,
        }
        logger.info(f"creating new service order with {data}")
        r = self._post("rest/acxservice/v1/ServiceOrder/New/SO", data)
        # the new SO won't be reflected in a cached list, so drop it
        self.invalidate_cache("service_orders")
        return r.json()

    def get_service_order_account_guid(self, soname):
        """
        gets service order and extracts it's Account.RefGUID attribute value
        """
        so = self.get_service_order(soname)
        return so.get("Account", {}).get("RefGUID", None)

    def activate_azure_service_order(
        self,
        so,
        vlanid,
        service_key,
    ):
        """
        activates Azure ExpressRoute service order

        so - service order being activated
        vlanid - vlanID on A side
        service_key - service key provided by Azure, needed for ExpressRoute provisioning
        """

        data = {
            "ActivatedDateTime": int(time()),
            "PartyA_VlanID": vlanid,
            "ServiceKey": service_key,
        }

        logger.info(f"activating Azure ExpressRoute service order {so} with {data}")
        r = self._patch(
            f"rest/acxservice/v1/ServiceOrder/ActivateBilling/MSXR/{so}", data
        )
        # status changed, cached list is now stale
        self.invalidate_cache("service_orders")
        return r.json()

    def activate_aws_service_order(
        self,
        so,
        vlanid,
        aws_dxcon_id,
        aws_account_id,
    ):
        """
        activates AWS service order

        so - service order being activated
        vlanid - vlanID on A side
        aws_dxcon_id - dxcon id
        aws_account_id (AWS ID of the owner account)
        """

        data = {
            "ActivatedDateTime": int(time()),
            "PartyA_VlanID": vlanid,
            "AccountID": aws_account_id,
            "AwsDxConID": aws_dxcon_id,
        }

        logger.info(f"activating AWS service order {so} with {data}")
        r = self._patch(
            f"rest/acxservice/v1/ServiceOrder/ActivateBilling/AWS/{so}", data
        )
        # status changed, cached list is now stale
        self.invalidate_cache("service_orders")
        return r.json()

    def activate_service_order(
        self,
        so,
        partyb_physical_so,
        partya_vlanid,
        partyb_vlanid,
        accepted_by_username,
    ):
        """
        activates the service order

        For cloud service activations partyb is the customer side, so
        partyb_physical_so is the service order representing the customer
        physical port and partyb_vlanid is the vlan on the customer side. For
        that reason the refguid needs to be extracted from the current service
        order rather than the partyb_physical_so, because the current service
        order is the one representing the customer side of the circuit.


        so - service order being activated
        partyb_physical_so - partyb physical SO
        partya_vlanid - vlanID on A side
        partyb_vlanid - vlanDI on B side
        accepted_by_username - username sending this request
        """
        contact_identifier = self.get_contact_identifier_by_username(
            accepted_by_username
        )
        if contact_identifier is None:
            raise OzoneClientError(
                f"contact identifier not found for username {accepted_by_username}"
            )

        partyb_account_guid = self.get_service_order_account_guid(so)
        if partyb_account_guid is None:
            raise OzoneClientError(f"account ID not found for service order {so}")

        data = {
            "ActivatedDateTime": int(time()),
            "PartyB_PhysicalSO": partyb_physical_so,
            "PartyB_AccountRefGUID": partyb_account_guid,
            "PartyA_VlanID": partya_vlanid,
            "PartyB_VlanID": partyb_vlanid,
            "AcceptedBy_ContactIdentifier": contact_identifier,
        }

        logger.info(f"activating service order {so} with {data}")
        r = self._patch(
            f"rest/acxservice/v1/ServiceOrder/ActivateBilling/Standard/{so}", data
        )
        # status changed, cached list is now stale
        self.invalidate_cache("service_orders")
        return r.json()

    def cancel_service_order(self, so, reason, username):
        """
        cancels an order
        so - service order to cancel
        reason - just text
        username - user cancelling the order
        """

        contact_identifier = self.get_contact_identifier_by_username(username)
        if contact_identifier is None:
            raise OzoneClientError(
                f"contact identifier not found for username {username}"
            )

        data = {
            "LastBillingDate": int(time()),
            "CancellationReason": reason,
            "ContactIdentifier": contact_identifier,
        }

        logger.info(f"cancelling service order {so} with {data}")
        r = self._patch(
            f"rest/acxservice/v1/ServiceOrder/RequestCancellation/{so}", data
        )
        # status changed, cached list is now stale
        self.invalidate_cache("service_orders")
        return r.json()
