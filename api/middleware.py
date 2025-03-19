""" Middleware functionality for application """
import os
from typing import Dict, List, Any
import traceback
import json
import re
import structlog
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Content, To, Email
from json2html import *
from config import settings


def field_name_modifier(
    logger: structlog._loggers.PrintLogger, log_method: str, event_dict: Dict
) -> Dict:
    """Changes the keys for some of the fields, to match Cloud Logging's expectations

    :return: Dict
    """
    event_dict["severity"] = event_dict["level"]
    del event_dict["level"]
    event_dict["message"] = event_dict["event"]
    del event_dict["event"]
    return event_dict


# set log level
log_level = structlog.stdlib._NAME_TO_LEVEL[
    os.getenv("LOG_LEVEL", "debug")
]
print(f"log level is {structlog.stdlib._LEVEL_TO_NAME[log_level]}")


def get_json_logger() -> structlog._config.BoundLoggerLazyProxy:
    """get_json_logger::extend using https://www.structlog.org/en/stable/processors.html """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            field_name_modifier,
            structlog.processors.TimeStamper("iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
    )
    return structlog.get_logger()


logger = get_json_logger()


def logging_flush() -> None:
    """
    logging_flush::Setting PYTHONUNBUFFERED in Dockerfile ensured no buffering
    """
    pass


def add_request_context_to_log(request_id: str) -> None:
    """
    Add a request ID to the structured logging context

    Args:
        request_id: Unique identifier for the request
    """
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)


def send_email(
    subject: str, receivers: List[str], template_path: str, params: Dict[str, Any]
) -> bool:
    """
    Send email using SendGrid API

    Args:
        subject: Email subject
        receivers: List of email recipients
        template_path: Path to the HTML template file
        params: Dictionary of parameters to replace in the template

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    if not receivers:
        logger.error("send_email:: No recipients provided")
        return False

    msg = {
        "subject": subject,
        "receivers": receivers,
        "template": template_path,
        "params": params,
    }

    # Extract secret values from custom-settings.toml file
    api_key = settings.sendgrid_api_key
    sender_email = settings.sendgrid_from_email

    if not api_key:
        logger.error(
            "send_email:: Missing SendGrid API key - set sendgrid_api_key in custom-settings.toml"
        )
        return False

    if not sender_email:
        logger.error(
            "send_email:: Missing sender email - set sendgrid_from_email in custom-settings.toml"
        )
        return False

    try:
        # Get the absolute path to the template file
        # This makes the path relative to the script's location
        # rather than the current working directory
        base_dir = os.path.dirname(os.path.abspath(__file__))
        absolute_template_path = os.path.join(base_dir, template_path)

        logger.debug(f"send_email:: Looking for template at: {absolute_template_path}")

        # Check if template file exists
        if not os.path.exists(absolute_template_path):
            logger.error(
                f"send_email:: Template file not found: {absolute_template_path}"
            )
            return False

        # Read and render the template
        with open(absolute_template_path, "r", encoding="utf-8") as tf:
            template_content = tf.read()

        # Simple template substitution - replace variables with values
        html_content = template_content
        for key, value in params.items():
            placeholder = "{{ " + key + " }}"
            html_content = html_content.replace(placeholder, str(value))

        # remove all unused template vars
        p = re.compile(r"{{.*?}}")
        html_content = p.sub("", html_content)

        # Create SendGrid message
        message = Mail(
            from_email=Email(sender_email),
            to_emails=[To(email) for email in receivers],
            subject=subject,
            html_content=Content("text/html", html_content),
        )

        # Create SendGrid client and send the email
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)

        logger.debug(
            "send_email:: Email sent successfully",
            status_code=response.status_code,
            mail=msg,
        )
        return True

    except FileNotFoundError as e:
        logger.error(
            "send_email:: Template file not found",
            template_path=absolute_template_path,
            error=str(e),
        )
        return False
    except Exception as e:
        # Log detailed error information
        logger.error(
            "send_email:: Email could not be sent",
            error_type=type(e).__name__,
            error_message=str(e),
            traceback=traceback.format_exc(),
            mail_subject=subject,
            mail_receivers=receivers,
            mail_template=template_path,
        )
        return False


# Only run this test code if the file is executed directly
if __name__ == "__main__":
    TEST_FILE="/app/test/example-payloads/get_entitlement_response.json"
    with open(TEST_FILE, "r", encoding="utf-8") as tfile:
        input = json.load(tfile)

    result = send_email(
        "New Entitlement Creation Request",
        ["markr.coley@gmail.com"],
        "templates/email/entitlement.html",
        {
            "title": "New Entitlement Creation Request",
            "headline": "A new entitlement creation request has been submitted:",
            "body": json2html.convert(json=input, clubbing=False),
            "footer": "If you did not subscribe to this, you may ignore this message.",
        },
    )

    if result:
        print("Email sent successfully")
    else:
        print("Failed to send email")
