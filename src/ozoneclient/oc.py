import logging
import json
from urllib3.util import Retry
from requests.adapters import HTTPAdapter
from requests.exceptions import HTTPError
import requests
from time import time
from functools import lru_cache

logger = logging.getLogger(__name__)

RETRIES_TOTAL = 3
RETRIES_BACKOFF_FACTOR = 2
RETRIES_STATUS_FORCELIST = [502, 503, 504]


class OzoneClientError(Exception):
    pass


class OzoneClient(object):
    # defaults to DEV environment
    def __init__(
        self, username, password, app_id, uri, app_name="ACX", timeout_seconds=15
    ):
        self.token = None
        # requests session parameter
        self.s = requests.Session()
        # retries class
        retries = Retry(
            total=RETRIES_TOTAL,
            backoff_factor=RETRIES_BACKOFF_FACTOR,
            status_forcelist=RETRIES_STATUS_FORCELIST,
            allowed_methods={"GET"},
        )
        self.s.mount("http://", HTTPAdapter(max_retries=retries))

        self.username = username
        self.password = password
        self.app_id = app_id
        self.app_name = app_name
        self.uri = uri

        self.timeout_seconds = timeout_seconds
        self._auth()

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
            logging.info("we're already authenticated...")
            return

        url = f"http://{self.uri}/rest/teracoczauthservice/v1/Authenticate"
        logging.info(f"trying to authenticate with ozone on {url}")
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
        logging.info("authenticated successfully...")
        self.token = r.json()
        self.s.headers.update(
            {
                "Authorization": self.token.get("AccessToken"),
                "Content-type": "application/json",
            }
        )

    def _get(self, path):
        url = f"http://{self.uri}/{path}"
        logging.info(f"sending authenticated GET request to: {url}")
        r = self.s.get(url, timeout=self.timeout_seconds)
        r.raise_for_status()
        return r

    def _post(self, path, data):
        url = f"http://{self.uri}/{path}"
        logging.info(f"sending authenticated POST request to: {url}")
        r = self.s.post(url, data=json.dumps(data), timeout=self.timeout_seconds)
        r.raise_for_status()
        return r

    def _patch(self, path, data):
        url = f"http://{self.uri}/{path}"
        logging.info(f"sending authenticated PATCH request to: {url}")
        r = self.s.patch(url, data=json.dumps(data), timeout=self.timeout_seconds)
        print(r.text)
        r.raise_for_status()
        return r

    def verify_user(self, username, password):
        logging.info(f"verifying user {username}")

        try:
            self._post(
                "rest/teracoczauthservice/v1/VerifyUser",
                {"Username": username, "Password": password},
            )
        except HTTPError as e:
            logging.info(
                f"failed to authenticate user {username}, return code: {e.response.status_code}, error: {e}"
            )
            return False
        except Exception as e:
            logging.warning(
                f"non standard exception detected while handling verification for user {username}: {e}"
            )
            return False

        return True

    @lru_cache(maxsize=None)
    def get_contacts(self):
        logging.info("getting all contacts")
        r = self._get("rest/acxservice/v1/Contact")
        return r.json()

    def get_contact(self, uid):
        logging.info(f"getting contact info for {uid}")
        r = self._get(f"rest/acxservice/v1/Contact/{uid}")
        return r.json()

    def get_contact_by_username(self, username):
        logging.info(f"getting contact info for {username}")
        contacts = self.get_contacts()
        # lets map all contacts into (full_contact, just_username)
        r = list(
            filter(
                lambda x: x[1] == username,
                map(lambda x: (x, x.get("Account", {}).get("Username"), ""), contacts),
            )
        )
        if len(r) == 0:
            return None
        # return the first instance remembering that it's a tuple where the first
        # element is the contact
        return r[0][0]

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
        logging.info(f"getting contacts for client identified by {guid}")
        r = self._get(f"rest/acxservice/v1/Contact/byClient/{guid}")
        return r.json()

    @lru_cache(maxsize=None)
    def get_clients(self):
        """
        cached
        """
        logging.info("getting all clients")
        r = self._get("rest/acxservice/v1/Client")
        return r.json()

    def get_client(self, guid):
        logging.info(f"getting client by refguid {guid}")
        r = self._get(f"rest/acxservice/v1/Client/{guid}")
        return r.json()

    @lru_cache(maxsize=None)
    def get_service_orders(self):
        logging.info("getting all service orders...")
        r = self._get("rest/acxservice/v1/ServiceOrder")
        return r.json()

    def get_service_order(self, so):
        """
        return service order by id (ServiceOrderId)
        """
        logging.info(f"getting service order: {so}")
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
        return r.json()

    def get_service_order_account_guid(self, soname):
        """
        gets service order and extracts it's Account.RefGUID attribute value
        """
        so = self.get_service_order(soname)
        return so.get("Account", {}).get("RefGUID", None)

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

        partyb_account_guid = self.get_service_order_account_guid(partyb_physical_so)
        if partyb_account_guid is None:
            raise OzoneClientError(
                f"account ID not found for service order {partyb_physical_so}"
            )

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
        return r.json()
