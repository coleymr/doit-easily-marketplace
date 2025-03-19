""" Module providing an interface for gcp account """
import json
from typing import Dict, Any

from dynaconf import Dynaconf
from json2html import *
from procurement_api import ProcurementApi
from middleware import logger, send_email

def handle_account(
    account_msg: Dict[str, Any], procurement_api: ProcurementApi, settings: Dynaconf
) -> None:
    """
    Handles incoming Pub/Sub messages about account resources.

    Args:
        account_msg: The account message from Pub/Sub
        procurement_api: An instance of the Procurement API client
        settings: Application configuration settings

    Returns:
        None
    """
    # Input validation
    if not account_msg or "id" not in account_msg:
        logger.error(
            "handle_account:: Invalid account message format", account_msg=account_msg
        )
        return

    logger.debug("handle_account", account_msg=account_msg)

    account_id = account_msg["id"]
    account = procurement_api.get_account(account_id)

    if not account:
        logger.debug(
            "handle_account:: account not found in procurement api, nothing to do"
        )
        return

    logger.debug(
        "handle_account:: checked procurement api for account",
        account=account,
        account_id=account_id,
    )

    # Look for the signup approval
    approval = None
    if "approvals" in account:
        for account_approval in account["approvals"]:
            if account_approval.get("name") == "signup":
                approval = account_approval
                break

    if not approval:
        logger.warning(
            "handle_account:: No signup approval found in account",
            account_id=account_id,
        )
        return

    # Get email recipients from settings
    recipients = getattr(settings, "email_recipients", [])
    if not recipients:
        logger.warning(
            "handle_account:: No email recipients configured, skipping email notifications"
        )
        return

    account_json = json.dumps(account).encode("utf-8")
    if approval["state"] == "PENDING":
        logger.info("handle_account:: account is pending, sending email")
        send_email(
            "New Account Pending Approval",
            recipients,
            "templates/email/account.html",
            {
                "title": "New Account is Pending Approval/Reject",
                "headline": "The following account is pending a response:",
                "body": json2html.convert(json=account_json, clubbing=False),
                "footer": "If you did not subscribe to this, you may ignore this message.",
            },
        )
        return

    if approval["state"] == "APPROVED":
        # TODO: store a customer record in database if needed
        logger.info("handle_account:: account is approved, sending confirmation email")
        send_email(
            "New Account Approved",
            recipients,
            "templates/email/account.html",
            {
                "title": "New Account has been approved",
                "headline": "The following account has been approved:",
                "body": json2html.convert(json=account_json, clubbing=False),
                "footer": "If you did not subscribe to this, you may ignore this message.",
            },
        )
        return

    # Log unknown approval states
    logger.warning(
        "handle_account:: Unknown approval state",
        account_id=account_id,
        approval_state=approval["state"],
    )
