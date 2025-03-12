import os
from typing import Dict, List, Any
import structlog
import traceback
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Content, To, Email

def field_name_modifier(logger: structlog._loggers.PrintLogger, log_method: str, event_dict: Dict) -> Dict:
    # Changes the keys for some of the fields, to match Cloud Logging's expectations
    event_dict["severity"] = event_dict["level"]
    del event_dict["level"]
    event_dict["message"] = event_dict["event"]
    del event_dict["event"]
    return event_dict


# Changed typo in comment from TOOD to TODO
log_level = structlog.stdlib._NAME_TO_LEVEL[os.getenv("LOG_LEVEL", "debug")]
print(f'log level is {structlog.stdlib._LEVEL_TO_NAME[log_level]}')


def get_json_logger() -> structlog._config.BoundLoggerLazyProxy:
    # extend using https://www.structlog.org/en/stable/processors.html
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            field_name_modifier,
            structlog.processors.TimeStamper("iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level)
    )
    return structlog.get_logger()


logger = get_json_logger()


def logging_flush() -> None:
    # Setting PYTHONUNBUFFERED in Dockerfile ensured no buffering
    pass


def add_request_context_to_log(request_id):
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)


def send_email(
    subject: str,
    receivers: List[str],
    template_path: str,
    params: Dict[str, Any]) -> bool:
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
    msg = {
        'subject': subject,
        'receivers': receivers,
        'template': template_path,
        'params': params
    }

    # Get API key from environment variable
    api_key = os.environ.get('SENDGRID_API_KEY')
    sender_email = os.environ.get('SENDER_EMAIL')

    if not api_key or not sender_email:
        logger.error("send_email:: Missing SendGrid configuration")
        return False

    try:
        # Read and render the template
        with open(template_path, 'r') as f:
            template_content = f.read()

        # Simple template substitution - replace variables with values
        html_content = template_content
        for key, value in params.items():
            placeholder = "{{" + key + "}}"
            html_content = html_content.replace(placeholder, str(value))

        # Create SendGrid message
        message = Mail(
            from_email=Email(sender_email),
            to_emails=[To(email) for email in receivers],
            subject=subject,
            html_content=Content("text/html", html_content)
        )

        # Create SendGrid client and send the email
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)

        logger.debug("send_email:: Email sent successfully",
                    status_code=response.status_code,
                    mail=msg)
        return True

    except Exception as e:
        # Log detailed error information
        logger.error("send_email:: Email could not be sent",
                    error_type=type(e).__name__,
                    error_message=str(e),
                    traceback=traceback.format_exc(),
                    mail_subject=subject,
                    mail_receivers=receivers,
                    mail_template=template_path)
        return False
