import os
import ssl
import traceback
from typing import Dict, Any, Optional, List, Union, Tuple

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

# Constants
PROCUREMENT_API = "cloudcommerceprocurement"
FIFTEEN_MINUTES = 900
API_VERSION = "v1"
DEFAULT_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
MAX_RETRIES = 8


class ProcurementApi:
    """Utilities for interacting with the Procurement API."""

    def __init__(self, project_id: str):
        """
        Initialize the Procurement API client.

        Args:
            project_id: The Google Cloud project ID

        Raises:
            Exception: If authentication fails with all methods
        """
        self.project_id = project_id

        try:
            # Try application default credentials first
            credentials, project = google.auth.default(scopes=DEFAULT_SCOPES)
            self.service = build(
                PROCUREMENT_API,
                API_VERSION,
                credentials=credentials,
                cache_discovery=False,
            )
            logger.info("Using application default credentials for Procurement API")
        except Exception as e:
            logger.warning(
                "Failed to use default credentials",
                error=str(e),
                traceback=traceback.format_exc(),
            )

            # Try explicit compute engine credentials
            try:
                credentials = compute_engine.Credentials(scopes=DEFAULT_SCOPES)
                self.service = build(
                    PROCUREMENT_API,
                    API_VERSION,
                    credentials=credentials,
                    cache_discovery=False,
                )
                logger.info("Using compute engine credentials for Procurement API")
            except Exception as e2:
                logger.error(
                    "Failed to authenticate with Procurement API",
                    error=str(e2),
                    traceback=traceback.format_exc(),
                )
                raise RuntimeError(
                    f"Failed to authenticate with Procurement API: {str(e2)}"
                )

    ##########################
    ### Account operations ###
    ##########################

    def get_account_id(self, name: str) -> str:
        """
        Extract account ID from a fully qualified account name.

        Args:
            name: Fully qualified account name (providers/PROJECT_ID/accounts/ACCOUNT_ID)

        Returns:
            The extracted account ID
        """
        if not name:
            logger.error("get_account_id:: Invalid account name", name=name)
            return ""

        prefix = f"providers/{self.project_id}/accounts/"
        if not name.startswith(prefix):
            logger.warning(
                "get_account_id:: Account name does not have expected format",
                name=name,
                expected_prefix=prefix,
            )
            # Try to extract ID regardless - fallback to splitting by '/'
            parts = name.split("/")
            return parts[-1] if parts else ""

        return name[len(prefix) :]

    def get_account_name(self, account_id: str) -> str:
        """
        Get the fully qualified name for an account ID.

        Args:
            account_id: The account ID

        Returns:
            The fully qualified account name
        """
        return f"providers/{self.project_id}/accounts/{account_id}"

    @on_exception(
        expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=MAX_RETRIES
    )
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def get_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        """
        Gets an account from the Procurement Service.

        Args:
            account_id: The account ID

        Returns:
            The account data if found, None otherwise
        """
        if not account_id:
            logger.error("get_account:: Missing account ID")
            return None

        logger.debug("get_account", account_id=account_id)
        name = self.get_account_name(account_id)
        request = self.service.providers().accounts().get(name=name)

        try:
            response = request.execute()
            return response
        except HttpError as err:
            logger.error(
                "get_account:: Error calling procurement API",
                exception=str(err),
                status_code=getattr(err.resp, "status", None),
                error_details=(
                    err.content.decode("utf-8") if hasattr(err, "content") else None
                ),
            )
            if hasattr(err, "resp") and err.resp.status == 404:
                return None
            raise

    @on_exception(
        expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=MAX_RETRIES
    )
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def approve_account(self, account_id: str) -> Dict[str, Any]:
        """
        Approves the account in the Procurement Service.

        Args:
            account_id: The account ID

        Returns:
            The API response
        """
        if not account_id:
            logger.error("approve_account:: Missing account ID")
            raise ValueError("Account ID is required")

        logger.debug("approve_account", account_id=account_id)
        name = self.get_account_name(account_id)
        request = (
            self.service.providers()
            .accounts()
            .approve(name=name, body={"approvalName": "signup"})
        )

        try:
            return request.execute()
        except HttpError as err:
            logger.error(
                "approve_account:: Error calling procurement API",
                exception=str(err),
                status_code=getattr(err.resp, "status", None),
                error_details=(
                    err.content.decode("utf-8") if hasattr(err, "content") else None
                ),
            )
            raise

    @on_exception(
        expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=MAX_RETRIES
    )
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def reset_account(self, account_id: str) -> Dict[str, Any]:
        """
        Resets the account in the Procurement Service.

        Args:
            account_id: The account ID

        Returns:
            The API response
        """
        if not account_id:
            logger.error("reset_account:: Missing account ID")
            raise ValueError("Account ID is required")

        logger.debug("reset_account", account_id=account_id)
        name = self.get_account_name(account_id)
        request = self.service.providers().accounts().reset(name=name)

        try:
            return request.execute()
        except HttpError as err:
            logger.error(
                "reset_account:: Error calling procurement API",
                exception=str(err),
                status_code=getattr(err.resp, "status", None),
                error_details=(
                    err.content.decode("utf-8") if hasattr(err, "content") else None
                ),
            )
            raise

    @on_exception(
        expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=MAX_RETRIES
    )
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def list_accounts(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Lists accounts from the Procurement Service.

        Args:
            account_id: Optional account ID to filter by

        Returns:
            The API response with the accounts list
        """
        # TODO: handle paging at some point

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
            logger.error(
                "list_accounts:: Error calling procurement API",
                exception=str(err),
                status_code=getattr(err.resp, "status", None),
                error_details=(
                    err.content.decode("utf-8") if hasattr(err, "content") else None
                ),
            )
            raise

    ##############################
    ### Entitlement operations ###
    ##############################

    def _get_entitlement_name(self, entitlement_id: str) -> str:
        """
        Get the fully qualified name for an entitlement ID.

        Args:
            entitlement_id: The entitlement ID

        Returns:
            The fully qualified entitlement name
        """
        return f"providers/{self.project_id}/entitlements/{entitlement_id}"

    def get_entitlement_id(self, name: str) -> str:
        """
        Extract entitlement ID from a fully qualified entitlement name.

        Args:
            name: Fully qualified entitlement name

        Returns:
            The extracted entitlement ID
        """
        if not name:
            logger.error("get_entitlement_id:: Invalid entitlement name", name=name)
            return ""

        # name is of format "providers/{providerId}/entitlements/{entitlement_id}"
        parts = name.split("/")
        if len(parts) < 4:
            logger.warning(
                "get_entitlement_id:: Entitlement name does not have expected format",
                name=name,
            )

        return parts[-1] if parts else ""

    @on_exception(
        expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=MAX_RETRIES
    )
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def get_entitlement(self, entitlement_id: str) -> Optional[Dict[str, Any]]:
        """
        Gets an entitlement from the Procurement Service.

        Args:
            entitlement_id: The entitlement ID

        Returns:
            The entitlement data if found, None otherwise
        """
        if not entitlement_id:
            logger.error("get_entitlement:: Missing entitlement ID")
            return None

        logger.debug("get_entitlement", entitlement_id=entitlement_id)
        name = self._get_entitlement_name(entitlement_id)
        request = self.service.providers().entitlements().get(name=name)

        try:
            response = request.execute()
            return response
        except HttpError as err:
            logger.error(
                "get_entitlement:: Error calling procurement API",
                exception=str(err),
                status_code=getattr(err.resp, "status", None),
                error_details=(
                    err.content.decode("utf-8") if hasattr(err, "content") else None
                ),
            )
            if hasattr(err, "resp") and err.resp.status == 404:
                return None
            raise

    @on_exception(
        expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=MAX_RETRIES
    )
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def approve_entitlement(self, entitlement_id: str) -> Dict[str, Any]:
        """
        Approves the entitlement in the Procurement Service.

        Args:
            entitlement_id: The entitlement ID

        Returns:
            The API response
        """
        if not entitlement_id:
            logger.error("approve_entitlement:: Missing entitlement ID")
            raise ValueError("Entitlement ID is required")

        logger.debug("approve_entitlement", entitlement_id=entitlement_id)
        name = self._get_entitlement_name(entitlement_id)
        request = self.service.providers().entitlements().approve(name=name, body={})

        try:
            return request.execute()
        except HttpError as err:
            logger.error(
                "approve_entitlement:: Error calling procurement API",
                exception=str(err),
                status_code=getattr(err.resp, "status", None),
                error_details=(
                    err.content.decode("utf-8") if hasattr(err, "content") else None
                ),
            )
            raise

    @on_exception(
        expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=MAX_RETRIES
    )
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def reject_entitlement(self, entitlement_id: str, reason: str) -> Dict[str, Any]:
        """
        Rejects the entitlement in the Procurement Service.

        Args:
            entitlement_id: The entitlement ID
            reason: The rejection reason

        Returns:
            The API response
        """
        if not entitlement_id:
            logger.error("reject_entitlement:: Missing entitlement ID")
            raise ValueError("Entitlement ID is required")

        if not reason:
            logger.warning(
                "reject_entitlement:: No reason provided", entitlement_id=entitlement_id
            )

        logger.debug("reject_entitlement", entitlement_id=entitlement_id, reason=reason)
        name = self._get_entitlement_name(entitlement_id)
        request = (
            self.service.providers()
            .entitlements()
            .reject(name=name, body={"reason": reason or "No reason provided"})
        )

        try:
            return request.execute()
        except HttpError as err:
            logger.error(
                "reject_entitlement:: Error calling procurement API",
                exception=str(err),
                status_code=getattr(err.resp, "status", None),
                error_details=(
                    err.content.decode("utf-8") if hasattr(err, "content") else None
                ),
            )
            raise

    @on_exception(
        expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=MAX_RETRIES
    )
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def approve_entitlement_plan_change(
        self, entitlement_id: str, new_pending_plan: str
    ) -> Dict[str, Any]:
        """
        Approves the entitlement plan change in the Procurement Service.

        Args:
            entitlement_id: The entitlement ID
            new_pending_plan: The new plan to approve

        Returns:
            The API response
        """
        if not entitlement_id:
            logger.error("approve_entitlement_plan_change:: Missing entitlement ID")
            raise ValueError("Entitlement ID is required")

        if not new_pending_plan:
            logger.error("approve_entitlement_plan_change:: Missing new pending plan")
            raise ValueError("New pending plan is required")

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

        try:
            return request.execute()
        except HttpError as err:
            logger.error(
                "approve_entitlement_plan_change:: Error calling procurement API",
                exception=str(err),
                status_code=getattr(err.resp, "status", None),
                error_details=(
                    err.content.decode("utf-8") if hasattr(err, "content") else None
                ),
            )
            raise

    @on_exception(
        expo, (RateLimitException, ssl.SSLError, HttpError), max_tries=MAX_RETRIES
    )
    @limits(calls=15, period=FIFTEEN_MINUTES)
    def list_entitlements(
        self, state: str = "ACTIVATION_REQUESTED", account_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Lists entitlements from the Procurement Service.

        Args:
            state: The entitlement state to filter by
            account_id: Optional account ID to filter by

        Returns:
            The API response with the entitlements list
        """
        # Build filter string
        filter_parts = []

        if state:
            filter_parts.append(f"state={state}")

        if account_id:
            filter_parts.append(f"account={account_id}")

        filter_str = " ".join(filter_parts) if filter_parts else None

        # TODO: handle paging at some point
        request = (
            self.service.providers()
            .entitlements()
            .list(parent=f"providers/{self.project_id}", filter=filter_str)
        )

        try:
            response = request.execute()
            return response
        except HttpError as err:
            logger.error(
                "list_entitlements:: Error calling procurement API",
                exception=str(err),
                status_code=getattr(err.resp, "status", None),
                error_details=(
                    err.content.decode("utf-8") if hasattr(err, "content") else None
                ),
            )
            raise


def is_account_approved(account: Optional[Dict[str, Any]]) -> bool:
    """
    Helper function to inspect the account to see if it's approved.

    Args:
        account: The account data from the Procurement API

    Returns:
        True if the account is approved, False otherwise
    """
    if not account:
        logger.warning("is_account_approved:: No account provided")
        return False

    if "approvals" not in account or not account["approvals"]:
        logger.debug("is_account_approved:: No approvals found in account")
        return False

    approval = None
    for account_approval in account["approvals"]:
        if account_approval.get("name") == "signup":
            approval = account_approval
            break

    logger.debug("is_account_approved:: Found approval", approval=approval)

    if not approval:
        logger.debug("is_account_approved:: No signup approval found")
        # The account has been deleted or never approved
        return False

    if approval.get("state") == "PENDING":
        logger.info("is_account_approved:: Account is pending approval")
        return False
    elif approval.get("state") == "APPROVED":
        logger.info("is_account_approved:: Account is approved")
        return True
    else:
        logger.warning(
            "is_account_approved:: Unknown approval state", state=approval.get("state")
        )
        return False
