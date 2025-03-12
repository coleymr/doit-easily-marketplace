import os
import json
from procurement_api import ProcurementApi
from middleware import logger, send_email
from flask_redmail import RedMail, EmailSender

from dynaconf import Dynaconf
from config import settings


def handle_account(
    account_msg: dict,
    procurement_api: ProcurementApi,
    email: RedMail,
    settings: Dynaconf):
    """Handles incoming Pub/Sub messages about account resources."""
    logger.debug("handle_account", account_msg=account_msg, settings=settings)

    account_id = account_msg["id"]
    account = procurement_api.get_account(account_id)

    if not account:
        # TODO: maybe delete customer account record in database
        logger.debug("handle_account:: account not found in procurement api, nothing to do")
        return

    logger.debug(
        "handle_account:: checked procurement api for entitlement",
        account=account,
        account_id=account_id,
    )

    approval = None
    for account_approval in account['approvals']:
        if account_approval['name'] == 'signup':
            approval = account_approval
            break

    if approval:
        if approval['state'] == 'PENDING':
            logger.info("handle_account:: account is pending sending email")
            send_email(
                email,
                'New Account Pending Approval',
                settings.email_recipients,
                'email/account.html',
                {
                    'title': 'New Account is Pending Approval/Reject',
                    'Headline': 'The following account is pending a response:',
                    'body': json.dumps(account, indent=4),
                },
            )
            return

        elif approval["state"] == "APPROVED":
            # TODO: store a customer record in database
            logger.info("handle_account:: account is approved sending email")
            send_email(
                email,
                'New Account Approved',
                settings.email_recipients,
                'email/account.html',
                {
                    'title': 'New Account has been approved',
                    'Headline': 'The following account has been approved:',
                    'body': json.dumps(account, indent=4),
                },
            )
            return
