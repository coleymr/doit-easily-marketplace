import json
import os
import sys
from typing import Dict, Any, Optional, Union
from unittest.mock import MagicMock
import requests
import traceback
from google.pubsub_v1 import PublisherClient

from procurement_api import ProcurementApi, is_account_approved
from middleware import logger, send_email
from dynaconf import Dynaconf


def notify(type: str, entitlement: Dict[str, Any], event_topic: str, publisher: PublisherClient) -> None:
    """
    Notify about entitlement changes via Pub/Sub.

    Args:
        type: The type of event (create, upgrade, destroy)
        entitlement: The entitlement data
        event_topic: The Pub/Sub topic to publish to
        publisher: The Pub/Sub publisher client
    """
    # TODO: in a SaaS model, this should call some service endpoint (provided via env) to create the service
    logger.info(
        "notify:: notify entitlement change",
        type=type,
        entitlement=entitlement,
        event_topic=event_topic,
    )
    try:
        if event_topic:
            data = json.dumps({"event": type, "entitlement": entitlement}).encode("utf-8")
            publisher.publish(event_topic, data)
        else:
            logger.warning("notify:: no event_topic configured, setup messages dropped")
    except Exception as e:
        logger.error(
            "notify:: failed to publish to topic",
            topic=event_topic,
            error=str(e),
            traceback=traceback.format_exc()
        )


def send_slack_message(webhook_url: str, entitlement: Dict[str, Any]) -> bool:
    """
    Send notification to Slack about entitlement events.

    Args:
        webhook_url: The Slack webhook URL
        entitlement: The entitlement data to include in the message

    Returns:
        bool: True if message was sent successfully, False otherwise
    """
    if not webhook_url:
        logger.warning("send_slack_message:: No webhook URL provided")
        return False

    title = "New Entitlement Creation Request"
    message = "A new entitlement creation request has been submitted"

    # Prepare the Slack message with blocks for better formatting
    slack_data = {
        "text": title,
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": title,
                },
            },
            {
                # "color": "#9733EE",
                "type": "section",
                "text": {"type": "mrkdwn", "text": json.dumps(entitlement, indent=4)},
            },
        ],
    }

    try:
        # Get content length for header
        byte_length = str(sys.getsizeof(slack_data))
        headers = {"Content-Type": "application/json", "Content-Length": byte_length}

        # Send the request with timeout
        response = requests.post(
            webhook_url,
            data=json.dumps(slack_data),
            headers=headers,
            timeout=10
        )

        if response.status_code != 200:
            logger.error(
                "send_slack_message:: failed to send message to slack",
                status_code=response.status_code,
                response_text=response.text,
            )
            # Commented out to maintain original behavior:
            # raise Exception(response.status_code, response.text)
            return False

        return True
    except requests.RequestException as e:
        logger.error(
            "send_slack_message:: error sending slack message",
            error=str(e),
            traceback=traceback.format_exc()
        )
        return False


# https://cloud.google.com/marketplace/docs/partners/integrated-saas/backend-integration#eventtypes
def handle_entitlement(
    event: Dict[str, Any],
    event_type: str,
    procurement_api: ProcurementApi,
    settings: Dynaconf,
    publisher: Optional[PublisherClient] = None
) -> None:
    """
    Handles incoming Pub/Sub messages about entitlement resources.

    Args:
        event: The entitlement event data
        event_type: The type of entitlement event
        procurement_api: The Procurement API client
        settings: Application configuration
        publisher: Optional Pub/Sub publisher client
    """
    # Input validation
    if not event or "id" not in event:
        logger.error("handle_entitlement:: Invalid event data", event=event)
        return

    if not event_type:
        logger.error("handle_entitlement:: Missing event type")
        return

    logger.debug("handle_entitlement", event_dict=event, event_type=event_type)

    # Get entitlement ID and details
    entitlement_id = event["id"]
    entitlement = procurement_api.get_entitlement(entitlement_id)

    if not entitlement:
        # Do nothing. The entitlement has to be canceled to be deleted, so
        # this has already been handled by a cancellation message.
        logger.debug("handle_entitlement:: entitlement not found in procurement api, nothing to do")
        return

    # Add ID to entitlement for easier reference
    entitlement["id"] = entitlement_id
    logger.debug(
        "handle_entitlement:: checked procurement api for entitlement",
        entitlement=entitlement,
        entitlement_id=entitlement_id,
    )

    # Get the product name from the entitlement object
    if "product" not in entitlement:
        logger.error("handle_entitlement:: entitlement missing product information", entitlement_id=entitlement_id)
        return

    product_name = entitlement["product"]
    logger.info("handle_entitlement:: entitlement for", product_name=product_name)

    # Get the first substring from a split using . as the separator.
    try:
        product_name = product_name.split(".")[0]

        # Load DynaConf settings for the product
        product_settings = settings.from_env(product_name)
    except Exception as e:
        logger.error(
            "handle_entitlement:: error getting product settings",
            product_name=product_name,
            error=str(e),
            traceback=traceback.format_exc()
        )
        return

    logger.debug(
        'handle_entitlement:: product config settings',
        product_name=product_name,
        event_topic=getattr(product_settings, 'event_topic', None),
        auto_approve_entitlements=getattr(product_settings, 'auto_approve_entitlements', False)
    )

    # Get account details
    if "account" not in entitlement:
        logger.error("handle_entitlement:: entitlement missing account information", entitlement_id=entitlement_id)
        return

    account_id = procurement_api.get_account_id(entitlement["account"])
    account = procurement_api.get_account(account_id)
    logger.debug("handle_entitlement:: account found", account=account)

    if not is_account_approved(account):
        # The account is not active so we cannot approve their entitlement.
        logger.warning(
            "handle_entitlement:: customer account is not approved, account must be approved using the frontend integration",
            account_id=account_id
        )
        return

    if "state" not in entitlement:
        logger.error("handle_entitlement:: entitlement missing state information", entitlement_id=entitlement_id)
        return

    entitlement_state = entitlement["state"]
    logger.debug("handle_entitlement:: entitlement state", state=entitlement_state)

    # Ensure we have a publisher if needed
    if getattr(product_settings, 'event_topic', None) and not publisher:
        logger.warning("handle_entitlement:: event_topic configured but no publisher provided")

    # Get email recipients
    email_recipients = getattr(product_settings, 'email_recipients', [])
    if not email_recipients:
        logger.warning("handle_entitlement:: no email recipients configured")

    # NOTE: because we don't persist any of this info to a local DB, there isn't much to do in this app.
    if event_type == "ENTITLEMENT_CREATION_REQUESTED":
        if entitlement_state == "ENTITLEMENT_ACTIVATION_REQUESTED":
            if getattr(product_settings, 'auto_approve_entitlements', False):
                logger.debug("handle_entitlement:: auto approving entitlement")
                procurement_api.approve_entitlement(entitlement_id)

            # TODO: we could send an update to the customer giving an approval timeline
            #  https://cloud.google.com/marketplace/docs/partners/integrated-saas/backend-integration#sending_a_status_message_to_users

            logger.debug("handle_entitlement:: sending email: New Entitlement Creation Request", entitlement=entitlement)

            if email_recipients:
                try:
                    send_email(
                        'New Entitlement Creation Request',
                        email_recipients,
                        'templates/email/entitlement.html',
                        {
                            'title': 'New Entitlement Creation Request',
                            'headline': 'A new entitlement creation request has been submitted:',
                            'body': json.dumps(entitlement, indent=4),
                        },
                    )
                except Exception as e:
                    logger.error(
                        "handle_entitlement:: error sending email",
                        error=str(e),
                        traceback=traceback.format_exc()
                    )

            # Send Slack notification if configured
            webhook_url = getattr(product_settings, 'slack_webhook', None)
            if webhook_url:
                send_slack_message(webhook_url, entitlement)

            # Nothing to do here, as the approval comes from the UI
            return

    elif event_type == "ENTITLEMENT_ACTIVE":
        if entitlement_state == "ENTITLEMENT_ACTIVE":
            event_topic = getattr(product_settings, 'event_topic', None)
            if event_topic and publisher:
                notify("create", entitlement, event_topic, publisher)
            return

    elif event_type == "ENTITLEMENT_PLAN_CHANGE_REQUESTED":
        if entitlement_state == "ENTITLEMENT_PENDING_PLAN_CHANGE_APPROVAL":
            # Don't write anything to our database until the entitlement
            # becomes active within the Procurement Service.
            if "newPendingPlan" in entitlement:
                procurement_api.approve_entitlement_plan_change(
                    entitlement_id, entitlement["newPendingPlan"]
                )
            else:
                logger.error("handle_entitlement:: missing newPendingPlan in entitlement", entitlement_id=entitlement_id)
            return

    elif event_type == "ENTITLEMENT_PLAN_CHANGED":
        if entitlement_state == "ENTITLEMENT_ACTIVE":
            event_topic = getattr(product_settings, 'event_topic', None)
            if event_topic and publisher:
                notify("upgrade", entitlement, event_topic, publisher)
            return

    elif event_type == "ENTITLEMENT_PLAN_CHANGE_CANCELLED":
        # Do nothing. We approved the original change, but we never recorded
        # it or changed the service level since it hadn't taken effect yet.
        return

    elif event_type == "ENTITLEMENT_CANCELLED":
        if entitlement_state == "ENTITLEMENT_CANCELLED":
            event_topic = getattr(product_settings, 'event_topic', None)
            if event_topic and publisher:
                return notify("destroy", entitlement, event_topic, publisher)
            return

    elif event_type == "ENTITLEMENT_PENDING_CANCELLATION":
        # Do nothing. We want to cancel once it's truly canceled. For now
        # it's just set to not renew at the end of the billing cycle.
        return

    elif event_type == "ENTITLEMENT_CANCELLATION_REVERTED":
        # Do nothing. The service was already active, but now it's set to
        # renew automatically at the end of the billing cycle.
        return

    elif event_type == "ENTITLEMENT_DELETED":
        # Do nothing. The entitlement has to be canceled to be deleted, so
        # this has already been handled by a cancellation message.
        return

    # When a customer purchases an offer
    elif event_type == "ENTITLEMENT_OFFER_ACCEPTED":
        if entitlement_state == "ENTITLEMENT_ACTIVATION_REQUESTED":
            logger.debug("handle_entitlement:: sending email: New Entitlement Offer Accepted", entitlement=entitlement)

            if email_recipients:
                try:
                    send_email(
                        'New Entitlement Offer Accepted',
                        email_recipients,
                        'templates/email/entitlement.html',
                        {
                            'title': 'New Entitlement Offer Accepted',
                            'headline': 'The following offer has been accepted:',
                            'body': json.dumps(entitlement, indent=4),
                        },
                    )
                except Exception as e:
                    logger.error(
                        "handle_entitlement:: error sending email",
                        error=str(e),
                        traceback=traceback.format_exc()
                    )
            return

    # TODO: handle ENTITLEMENT_OFFER_ENDED for private offers?
    #  Indicates that a customer's private offer has ended. The offer either triggers an ENTITLEMENT_CANCELLED event or remains active with non-discounted pricing.

    # If we reach here, log that the event wasn't handled
    logger.warning(
        "handle_entitlement:: unhandled event type or state combination",
        event_type=event_type,
        entitlement_state=entitlement_state
    )
    return
