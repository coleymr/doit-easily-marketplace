""" Backend application """

import base64
import os
import json
import uuid
import traceback

from typing import Dict
from flask import request, Flask, render_template, jsonify, session
from google.cloud import pubsub_v1
import jwt
import requests

from cryptography.x509 import load_pem_x509_certificate
from middleware import logger, add_request_context_to_log
from requests.exceptions import HTTPError, ConnectionError
from procurement_api import ProcurementApi, is_account_approved
from account import handle_account
from entitlement import handle_entitlement
from config import settings

app = Flask(__name__)

# Implement Flask Sessions
app.secret_key = settings.FLASK_SECRET_KEY

# Register Global Jinja2 Functions
app.jinja_env.globals.update(is_account_approved=is_account_approved)

# Initialize global services
publisher = pubsub_v1.PublisherClient()
procurement_api = ProcurementApi(settings.MARKETPLACE_PROJECT)

# Email config
app.config["EMAIL_HOST"] = settings.EMAIL_HOST
app.config["EMAIL_PORT"] = settings.EMAIL_PORT
app.config["EMAIL_SENDER"] = settings.EMAIL_SENDER
app.config["SESSION_COOKIE_HTTPONLY"] = settings.SESSION_COOKIE_HTTPONLY # Prevents JS access to cookies
app.config["SESSION_COOKIE_SECURE"] = settings.SESSION_COOKIE_SECURE  # Sends cookie only over HTTPS
app.config["SESSION_COOKIE_SAMESITE"] = settings.SESSION_COOKIE_SAMESITE  # Prevents cross-site CSRF

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
    request_id = str(uuid.uuid4())
    add_request_context_to_log(request_id)

    # First attempt: Try getting decoded JWT claims from the session.
    decoded_claims = session.get("jwt_claims")

    # If the session doesn't contain a valid JWT, try to retrieve the token from the request.
    if not decoded_claims or "sub" not in decoded_claims:
        token = request.form.get("x-gcp-marketplace-token") or request.args.get("x-gcp-marketplace-token")
        if token:
            try:
                decoded_claims = verify_marketplace_jwt(token)
                # Optionally, store the decoded claims in session for future use.
                session["jwt_claims"] = decoded_claims
            except Exception as e:
                logger.error("login:: JWT validation failed from provided token",
                             extra={"error": str(e), "request_id": request_id})
                return jsonify({"error": "Authentication error, invalid JWT token"}), 401
        else:
            logger.error("login:: JWT claims missing from session and token not provided",
                         extra={"request_id": request_id})
            return jsonify({"error": "Authentication error, missing or invalid JWT claims"}), 401

    account_id = decoded_claims["sub"]

    try:
        # Approve the account
        approve_account_api(account_id)
        logger.info("login:: account approved", extra={"account_id": account_id, "request_id": request_id})

        # Get the entitlement ID for the account
        pending_creation_requests = procurement_api.list_entitlements(account_id=account_id)
        for pcr in pending_creation_requests.get("entitlements", []):
            entitlement_id = procurement_api.get_entitlement_id(pcr["name"])

        # If auto_approve_entitlements is enabled, approve the entitlement
        if settings.auto_approve_entitlements:
            procurement_api.approve_entitlement(entitlement_id)

        # Render a success page telling the customer what to do next
        page_context = {"entitlement_id": entitlement_id}
        nav = {"tooltip_title": "Account Approved", "tooltip_url": ""}
        return render_template("login.html", **page_context, nav=nav)
    except Exception as e:
        logger.error("login:: account approval failed", extra={"error": str(e), "request_id": request_id})
        return jsonify({"error": "Account approval failed"}), 500
    finally:
        # Clear the session data regardless of success or failure
        session.pop("jwt_claims", None)


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

def verify_marketplace_jwt(encoded_jwt: str):
    header = jwt.get_unverified_header(encoded_jwt)
    key_id = header.get("kid")
    unverified_decoded = jwt.decode(encoded_jwt, options={"verify_signature": False})
    url = unverified_decoded["iss"]

    if url != GOOGLE_CERT_URL:
        raise ValueError("Invalid issuer URL")

    certs = requests.get(url=url).json()
    cert = certs.get(key_id)
    if not cert:
        raise ValueError("Certificate not found")

    cert_obj = load_pem_x509_certificate(cert.encode('utf-8'))
    public_key = cert_obj.public_key()

    decoded = jwt.decode(encoded_jwt, public_key, algorithms=["RS256"], audience=settings.AUDIENCE)

    if not decoded.get("sub"):
        raise ValueError("Missing sub claim")

    return decoded

def approve_account_api(account_id):
    try:
        return procurement_api.approve_account(account_id)
    except HTTPError as http_err:
        logger.exception("Error approving account due to HTTP error", extra={"account_id": account_id})
        raise
    except Exception as exc:
        logger.exception("Unexpected error approving account", extra={"account_id": account_id})
        raise

# Registration/Signup
@app.route("/registration", methods=["POST"])
@app.route("/signup", methods=["POST"])
def register():
    request_id = str(uuid.uuid4())
    add_request_context_to_log(request_id)

    encoded = request.form.get("x-gcp-marketplace-token")
    if not encoded:
        logger.error('signup:: missing token')
        return "Missing token", 401

    try:
        decoded_claims = verify_marketplace_jwt(encoded)
        logger.debug('signup:: JWT validated', sub=decoded_claims["sub"])

        # Save claims securely in session
        session["jwt_claims"] = decoded_claims

        # You can now safely remove token from client-side forms
        page_context = {
            "request_id": request_id,
            "jwt_claims": decoded_claims
        }
        return render_template("signup.html", **page_context)

    except Exception as e:
        logger.error('signup:: JWT validation failed', error=str(e))
        return "JWT validation failed", 401

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
