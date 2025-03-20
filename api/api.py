""" Backend application """

import base64
import os
import json
import uuid
import traceback

from typing import Dict
from flask import request, Flask, render_template, jsonify
from google.cloud import pubsub_v1
import jwt
import requests

from cryptography.x509 import load_pem_x509_certificate
from middleware import logger, add_request_context_to_log
from procurement_api import ProcurementApi, is_account_approved
from account import handle_account
from entitlement import handle_entitlement
from config import settings

app = Flask(__name__)

# Register Global Jinja2 Functions
app.jinja_env.globals.update(is_account_approved=is_account_approved)

# Initialize global services
publisher = pubsub_v1.PublisherClient()
procurement_api = ProcurementApi(settings.MARKETPLACE_PROJECT)

# Email config
app.config["EMAIL_HOST"] = settings.EMAIL_HOST
app.config["EMAIL_PORT"] = settings.EMAIL_PORT
app.config["EMAIL_SENDER"] = settings.EMAIL_SENDER

# Constants
GOOGLE_CERT_URL = "https://www.googleapis.com/robot/v1/metadata/x509/cloud-commerce-partner@system.gserviceaccount.com"

# Valid entitlement states for filtering
entitlement_states = [
    "CREATION_REQUESTED",
    "ACTIVE",
    "PLAN_CHANGE_REQUESTED",
    "PLAN_CHANGED",
    "PLAN_CHANGE_CANCELLED",
    "CANCELLED",
    "PENDING_CANCELLATION",
    "CANCELLATION_REVERTED",
    "DELETED",
]


# Web UI routes


@app.route("/app")
def entitlements():
    """Display entitlements page filtered by state."""
    request_id = str(uuid.uuid4())
    add_request_context_to_log(request_id)

    try:
        state = request.args.get("state", "ACTIVATION_REQUESTED")
        page_context = {"request_id": request_id}

        logger.debug("entitlements:: loading index", state=state)

        # Get entitlements based on state
        if state not in entitlement_states:
            entitlement_response = procurement_api.list_entitlements()
        else:
            entitlement_response = procurement_api.list_entitlements(state=state)

        logger.debug(
            "entitlements:: entitlements loaded", entitlements=entitlement_response
        )

        # Extract entitlements from response
        page_context["entitlements"] = entitlement_response.get("entitlements", [])

        # Navigation context
        nav = {"tooltip_title": "Entitlement Requests", "tooltip_url": ""}

        return render_template("index.html", **page_context, nav=nav)
    except Exception as e:
        logger.error(
            "entitlements:: error loading entitlements",
            error=str(e),
            traceback=traceback.format_exc(),
        )
        return jsonify({"error": "Loading failed"}), 500


@app.route("/accounts")
def accounts():
    """Display accounts page."""
    request_id = str(uuid.uuid4())
    add_request_context_to_log(request_id)

    try:
        page_context = {"request_id": request_id}
        logger.debug("accounts:: loading accounts")

        account_response = procurement_api.list_accounts()
        logger.debug("accounts:: accounts loaded", accounts=account_response)

        # Add accounts data to context
        page_context["accounts"] = account_response.get("accounts", [])

        # Navigation context
        nav = {"tooltip_title": "Non Approved Accounts", "tooltip_url": ""}

        return render_template("accounts.html", **page_context, nav=nav)
    except Exception as e:
        logger.error(
            "accounts:: error loading accounts",
            error=str(e),
            traceback=traceback.format_exc(),
        )
        return jsonify({"error": "Loading failed"}), 500


@app.route("/app/account/<account_id>")
def show_account(account_id: str):
    """Display details for a specific account."""
    request_id = str(uuid.uuid4())
    add_request_context_to_log(request_id)

    try:
        page_context = {"request_id": request_id}
        logger.debug("show_account:: loading account page", account_id=account_id)

        # Validate account_id
        if not account_id:
            page_context["error"] = "No account ID provided"
            return render_template("account.html", **page_context), 400

        # Get account details
        account = procurement_api.get_account(account_id)

        if not account:
            page_context["error"] = "Account not found"
            return render_template("account.html", **page_context), 404

        # Add account data to context
        page_context["account"] = account
        page_context["account"]["is_approved"] = is_account_approved(account)

        # Navigation context
        nav = {
            "tooltip_title": f"Account {account['name'].split('/')[-1]}",
            "tooltip_url": "/app",
        }

        return render_template("account.html", **page_context, nav=nav)
    except Exception as e:
        logger.error(
            "show_account:: error loading account",
            error=str(e),
            traceback=traceback.format_exc(),
            account_id=account_id,
        )
        return jsonify({"error": "Loading failed"}), 500


@app.route("/login", methods=["POST"])
@app.route("/activate", methods=["POST"])
def login():
    """Handle login and account activation."""
    request_id = str(uuid.uuid4())
    add_request_context_to_log(request_id)

    try:
        # Get and validate the marketplace token
        encoded = request.form.get("x-gcp-marketplace-token")
        logger.debug("login:: processing token", token_present=bool(encoded))

        if not encoded:
            logger.error("login:: missing token in request")
            return "Invalid token", 401

        # Parse JWT header without verifying signature
        header = jwt.get_unverified_header(encoded)
        key_id = header.get("kid")

        if not key_id:
            logger.error("login:: missing kid in token header")
            return "Invalid token format", 401

        # Decode JWT without verifying signature to get issuer
        unverified_decoded = jwt.decode(encoded, options={"verify_signature": False})
        url = unverified_decoded.get("iss")

        # Verify the issuer
        if url != GOOGLE_CERT_URL:
            logger.error("login:: invalid issuer", issuer=url, expected=GOOGLE_CERT_URL)
            return "Invalid token issuer", 401

        # Get the certificate from Google
        try:
            certs = requests.get(url=url, timeout=1).json()
            cert = certs.get(key_id)

            if not cert:
                logger.error("login:: certificate not found", key_id=key_id)
                return "Certificate not found", 401

            cert_obj = load_pem_x509_certificate(bytes(cert, "utf-8"))
            public_key = cert_obj.public_key()
        except Exception as e:
            logger.error(
                "login:: failed to get certificate",
                error=str(e),
                traceback=traceback.format_exc(),
            )
            return "Failed to verify token", 401

        # Verify the JWT signature
        try:
            decoded = jwt.decode(
                encoded, public_key, algorithms=["RS256"], audience=settings.AUDIENCE
            )
        except jwt.exceptions.InvalidAudienceError:
            logger.error("login:: audience mismatch")
            return "Audience mismatch", 401
        except jwt.exceptions.ExpiredSignatureError:
            logger.error("login:: token expired")
            return "Token expired", 401
        except Exception as e:
            logger.error(
                "login:: token validation failed",
                error=str(e),
                traceback=traceback.format_exc(),
            )
            return "Token validation failed", 401

        # Verify subject is present
        if not decoded.get("sub"):
            logger.error("login:: subject is empty")
            return "Subject empty", 401

        # JWT validated, approve account
        account_id = decoded["sub"]
        logger.debug("login:: approving account", account=account_id)

        # Approve the account
        response = procurement_api.approve_account(account_id)
        logger.info("login:: procurement api approve complete", response=response)

        # Auto-approve entitlements if configured
        if settings.auto_approve_entitlements:
            try:
                # Get pending entitlement creation requests
                pending_creation_requests = procurement_api.list_entitlements(
                    account_id=account_id
                )
                logger.debug(
                    "login:: pending requests",
                    pending_creation_requests=pending_creation_requests,
                )

                # Approve each pending entitlement
                pending_entitlements = pending_creation_requests.get("entitlements", [])
                for pcr in pending_entitlements:
                    entitlement_id = procurement_api.get_entitlement_id(pcr["name"])
                    logger.info(
                        "login:: approving entitlement", entitlement_id=entitlement_id
                    )
                    procurement_api.approve_entitlement(entitlement_id)

                logger.info(
                    "login:: approved entitlements", count=len(pending_entitlements)
                )
            except Exception as e:
                logger.error(
                    "login:: error approving entitlements",
                    error=str(e),
                    traceback=traceback.format_exc(),
                )
                # Continue execution despite entitlement approval errors

        return "Your account has been approved. You can close this window.", 200

    except Exception as e:
        logger.error(
            "login:: an exception occurred",
            error=str(e),
            traceback=traceback.format_exc(),
        )
        return jsonify({"error": "Failed to approve account"}), 500


# API routes
@app.route("/v1/entitlements", methods=["GET"])
def index():
    """API endpoint to list entitlements."""
    request_id = str(uuid.uuid4())
    add_request_context_to_log(request_id)

    try:
        state = request.args.get("state", "ACTIVATION_REQUESTED")

        if state not in entitlement_states:
            return procurement_api.list_entitlements()

        return procurement_api.list_entitlements(state=state)
    except Exception as e:
        logger.error(
            "index:: an exception occurred listing entitlements",
            error=str(e),
            traceback=traceback.format_exc(),
        )
        return jsonify({"error": "Procurement API call failed"}), 500


@app.route("/v1/entitlement/<entitlement_id>/approve", methods=["POST"])
def entitlement_approve(entitlement_id: str):
    """API endpoint to approve an entitlement."""
    request_id = str(uuid.uuid4())
    add_request_context_to_log(request_id)

    logger.info(
        "entitlement_approve:: approving entitlement", entitlement_id=entitlement_id
    )

    try:
        if not entitlement_id:
            return jsonify({"error": "Missing entitlement ID"}), 400

        procurement_api.approve_entitlement(entitlement_id)
        return jsonify({}), 200
    except Exception as e:
        logger.error(
            "entitlement_approve:: an exception occurred",
            error=str(e),
            traceback=traceback.format_exc(),
            entitlement_id=entitlement_id,
        )
        return jsonify({"error": "Approve failed"}), 500


@app.route("/v1/entitlement/<entitlement_id>/reject", methods=["POST"])
def entitlement_reject(entitlement_id: str):
    """API endpoint to reject an entitlement."""
    request_id = str(uuid.uuid4())
    add_request_context_to_log(request_id)

    logger.info(
        "entitlement_reject:: rejecting entitlement", entitlement_id=entitlement_id
    )

    try:
        if not entitlement_id:
            return jsonify({"error": "Missing entitlement ID"}), 400

        # Get reason from request body
        msg_json = request.json

        if not msg_json or "reason" not in msg_json:
            return jsonify({"error": "Missing rejection reason"}), 400

        procurement_api.reject_entitlement(entitlement_id, msg_json["reason"])
        return jsonify({}), 200
    except Exception as e:
        logger.error(
            "entitlement_reject:: an exception occurred",
            error=str(e),
            traceback=traceback.format_exc(),
            entitlement_id=entitlement_id,
        )
        return jsonify({"error": "Reject failed"}), 500


@app.route("/v1/account/<account_id>/approve", methods=["POST"])
def account_approve(account_id: str):
    """API endpoint to approve an account."""
    request_id = str(uuid.uuid4())
    add_request_context_to_log(request_id)

    logger.info("account_approve:: approving account", account_id=account_id)

    try:
        if not account_id:
            return jsonify({"error": "Missing account ID"}), 400

        response = procurement_api.approve_account(account_id)
        logger.info(
            "account_approve:: procurement api approve complete", response=response
        )
        return jsonify({}), 200
    except Exception as e:
        logger.error(
            "account_approve:: an exception occurred",
            error=str(e),
            traceback=traceback.format_exc(),
            account_id=account_id,
        )
        return jsonify({"error": "Approve failed"}), 500


@app.route("/v1/account/<account_id>/reset", methods=["POST"])
def account_reset(account_id: str):
    """API endpoint to reset an account."""
    request_id = str(uuid.uuid4())
    add_request_context_to_log(request_id)

    logger.info("account_reset:: resetting account", account_id=account_id)

    try:
        if not account_id:
            return jsonify({"error": "Missing account ID"}), 400

        response = procurement_api.reset_account(account_id)
        logger.info("account_reset:: procurement api reset complete", response=response)
        return jsonify({}), 200
    except Exception as e:
        logger.error(
            "account_reset:: an exception occurred",
            error=str(e),
            traceback=traceback.format_exc(),
            account_id=account_id,
        )
        return jsonify({"error": "Reset failed"}), 500


@app.route("/v1/notification", methods=["POST"])
def handle_subscription_message():
    """Handle notification messages from Pub/Sub."""
    request_id = str(uuid.uuid4())
    add_request_context_to_log(request_id)

    logger.debug("handle_subscription_message:: event received")

    try:
        # Get and validate the message envelope
        envelope = request.json

        if not envelope:
            logger.warning("handle_subscription_message:: no Pub/Sub message received")
            return jsonify({}), 200

        if not isinstance(envelope, Dict) or "message" not in envelope:
            logger.warning(
                "handle_subscription_message:: invalid Pub/Sub message format"
            )
            return jsonify({}), 200

        # Extract the message data
        message = envelope["message"]

        if not isinstance(message, Dict) or "data" not in message:
            logger.warning("handle_subscription_message:: no data in message")
            return jsonify({}), 200

        # Decode and parse the message data
        try:
            # decode b64, decode utf-8, strip, json parse
            message_data = message["data"]
            decoded_data = base64.b64decode(message_data).decode("utf-8").strip()
            message_json = json.loads(decoded_data)
            logger.debug(
                "handle_subscription_message:: message parsed",
                message_json=message_json,
            )
        except Exception as e:
            logger.error(
                "handle_subscription_message:: failure decoding data",
                error=str(e),
                traceback=traceback.format_exc(),
            )
            return jsonify({}), 200

        # Process entitlement or account messages
        if "entitlement" in message_json and "eventType" in message_json:
            logger.debug(
                "handle_subscription_message:: processing entitlement",
                event_type=message_json["eventType"],
            )

            handle_entitlement(
                message_json["entitlement"],
                message_json["eventType"],
                procurement_api,
                settings,
                publisher,
            )
        elif "account" in message_json:
            logger.debug("handle_subscription_message:: processing account")

            handle_account(
                message_json["account"],
                procurement_api,
                settings,
            )
        else:
            logger.warning(
                "handle_subscription_message:: no account or entitlement in message"
            )

        return jsonify({}), 200

    except Exception as e:
        logger.error(
            "handle_subscription_message:: an exception occurred",
            error=str(e),
            traceback=traceback.format_exc(),
        )
        # Always return 200 for Pub/Sub to avoid redelivery
        return jsonify({}), 200


# Registration/Signup
@app.route("/registration")
@app.route("/signup")
def register():
    """Display signup page."""
    request_id = str(uuid.uuid4())
    add_request_context_to_log(request_id)

    try:
        page_context = {"request_id": request_id}
        logger.debug("register:: loading signup page")

        return render_template("signup.html", **page_context)
    except Exception as e:
        logger.error(
            "register:: error loading signup page",
            error=str(e),
            traceback=traceback.format_exc(),
        )
        return jsonify({"error": "Loading failed"}), 500


@app.route("/alive")
def alive():
    """Health check endpoint."""
    return "", 200


if __name__ == "__main__":
    # Get port from environment variable with fallback to 8080
    port = int(os.environ.get("PORT", 8080))

    # In production, set debug=False
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    app.run(debug=debug_mode, host="0.0.0.0", port=port)
