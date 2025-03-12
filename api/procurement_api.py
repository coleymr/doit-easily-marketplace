import os
import ssl

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpMock
from ratelimit import limits, RateLimitException
from backoff import on_exception, expo
from middleware import logger
from unittest import mock
from google.auth import compute_engine
from google.oauth2 import service_account
import google.auth

from config import settings

PROCUREMENT_API = "cloudcommerceprocurement"
FIFTEEN_MINUTES = 900


class ProcurementApi(object):
    """Utilities for interacting with the Procurement API."""

    # def __init__(self, project_id):
    #     self.service = build(PROCUREMENT_API, "v1", cache_discovery=False)
    #     self.project_id = project_id

    def __init__(self, project_id):
        self.project_id = project_id

        try:
            # Try application default credentials first
            credentials, project = google.auth.default(
                scopes=['https://www.googleapis.com/auth/cloud-platform']
            )
            self.service = build(PROCUREMENT_API, "v1", credentials=credentials, cache_discovery=False)
            logger.info("Using application default credentials for Procurement API")
        except Exception as e:
            logger.warning(f"Failed to use default credentials: {str(e)}")

            # Try explicit compute engine credentials
            try:
                credentials = compute_engine.Credentials(
                    scopes=['https://www.googleapis.com/auth/cloud-platform']
                )
                self.service = build(PROCUREMENT_API, "v1", credentials=credentials, cache_discovery=False)
                logger.info("Using compute engine credentials for Procurement API")
            except Exception as e2:
                logger.error(f"Failed to authenticate with Procurement API: {str(e2)}")
                raise

    ##########################
    ### Account operations ###
    ##########################

    def get_account_id(self, name):
        # name is of format "providers/DEMO-project_id/accounts/12345"
        return name[len(f"providers/{self.project_id}/accounts/") :]

    def get_account_name(self, account_id):
        return f"providers/{self.project_id}/accounts/{account_id}"

    @on_exception(expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=8)
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def get_account(self, account_id):
        """Gets an account from the Procurement Service."""
        logger.debug("get_account", account_id=account_id)
        name = self.get_account_name(account_id)
        request = self.service.providers().accounts().get(name=name)
        try:
            response = request.execute()
            return response
        except HttpError as err:
            logger.error(f"error calling procurement api", exception=err)
            if err.resp.status == 404:
                return None

    @on_exception(expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=8)
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def approve_account(self, account_id):
        """Approves the account in the Procurement Service."""
        logger.debug("approve_account", account_id=account_id)
        name = self.get_account_name(account_id)
        request = (
            self.service.providers()
            .accounts()
            .approve(name=name, body={"approvalName": "signup"})
        )
        return request.execute()

    @on_exception(expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=8)
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def reset_account(self, account_id):
        """Resets the account in the Procurement Service."""
        logger.debug("reset_account", account_id=account_id)
        name = self.get_account_name(account_id)
        request = self.service.providers().accounts().reset(name=name)
        return request.execute()

    @on_exception(expo, RateLimitException, max_tries=8)
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def list_accounts(self, account_id=None):
        # todo, maybe need to handle paging at some point
        request = (
            self.service.providers()
            .accounts()
            .list(
                parent=f"providers/{self.project_id}",
            )
        )
        try:
            response = request.execute()
            return response
        except HttpError as err:
            logger.error(f"error calling procurement api", exception=err)
            raise err

    ##############################
    ### Entitlement operations ###
    ##############################

    def _get_entitlement_name(self, entitlement_id):
        return (
            f"providers/{self.project_id}/entitlements/{entitlement_id}"
        )

    def get_entitlement_id(self, name):
        # name is of format "providers/{providerId}/entitlements/{entitlement_id}"
        return name.split("/")[-1]

    @on_exception(expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=8)
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def get_entitlement(self, entitlement_id):
        """Gets an entitlement from the Procurement Service."""
        logger.debug("get_entitlement", entitlement_id=entitlement_id)
        name = self._get_entitlement_name(entitlement_id)
        request = self.service.providers().entitlements().get(name=name)
        try:
            response = request.execute()
            return response
        except HttpError as err:
            logger.error(f"error calling procurement api", exception=err)
            if err.resp.status == 404:
                return None

    @on_exception(expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=8)
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def approve_entitlement(self, entitlement_id):
        """Approves the entitlement in the Procurement Service."""
        logger.debug("approve_entitlement", entitlement_id=entitlement_id)
        name = self._get_entitlement_name(entitlement_id)
        request = self.service.providers().entitlements().approve(name=name, body={})
        request.execute()

    @on_exception(expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=8)
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def reject_entitlement(self, entitlement_id, reason):
        """Rejects the entitlement in the Procurement Service."""
        logger.debug("reject_entitlement", entitlement_id=entitlement_id)
        name = self._get_entitlement_name(entitlement_id)
        request = (
            self.service.providers()
            .entitlements()
            .reject(name=name, body={"reason": reason})
        )
        request.execute()

    @on_exception(expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=8)
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def approve_entitlement_plan_change(self, entitlement_id, new_pending_plan):
        """Approves the entitlement plan change in the Procurement Service."""
        logger.debug(
            "approve_entitlement_plan_change",
            entitlement_id=entitlement_id,
            new_pending_plan=new_pending_plan,
        )
        name = self._get_entitlement_name(entitlement_id)
        body = {"pendingPlanName": new_pending_plan}
        request = (
            self.service.providers()
            .entitlements()
            .approvePlanChange(name=name, body=body)
        )
        request.execute()

    @on_exception(expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=8)
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def list_entitlements(self, state="ACTIVATION_REQUESTED", account_id=None):
        account_filter = f" account={account_id}" if account_id else ""
        # todo, maybe need to handle paging at some point
        request = (
            self.service.providers()
            .entitlements()
            .list(
                parent=f"providers/{self.project_id}",
                filter=f"state={state}{account_filter}",
            )
        )
        try:
            response = request.execute()
            return response
        except HttpError as err:
            logger.error(f"error calling procurement api", exception=err)
            raise err

def is_account_approved(account: dict) -> bool:
    """Helper function to inspect the account to see if its approved"""

    approval = None
    for account_approval in account["approvals"]:
        if account_approval["name"] == "signup":
            approval = account_approval
            break
    logger.debug("found approval", approval=approval)

    if approval:
        if approval["state"] == "PENDING":
            logger.info("account is pending")
            return False
        elif approval["state"] == "APPROVED":
            logger.info("account is approved")
            return True
    else:
        logger.debug("no approval found")
        # The account has been deleted
        return False
