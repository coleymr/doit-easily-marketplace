import os
from typing import Dict
import structlog
import traceback
from flask_redmail import RedMail, EmailSender
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from jinja2 import Environment, FileSystemLoader
from flask import current_app

def field_name_modifier(logger: structlog._loggers.PrintLogger, log_method: str, event_dict: Dict) -> Dict:
    # Changes the keys for some of the fields, to match Cloud Logging's expectations
    event_dict["severity"] = event_dict["level"]
    del event_dict["level"]
    event_dict["message"] = event_dict["event"]
    del event_dict["event"]
    return event_dict


# TOOD: this log level setting needs to be tested
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
    email: RedMail,  # Keep this parameter for compatibility
    subject: str,
    receivers: list,
    template: str,
    params: dict) -> bool:

    try:
        # Get email configuration from Flask app
        app = current_app
        smtp_host = app.config.get("EMAIL_HOST")
        smtp_port = app.config.get("EMAIL_PORT")
        smtp_user = app.config.get("EMAIL_USERNAME", "")
        smtp_pass = app.config.get("EMAIL_PASSWORD", "")
        sender_email = app.config.get("EMAIL_SENDER")

        # Create message
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = ", ".join(receivers)

        # Load and render the template
        env = Environment(loader=FileSystemLoader('templates'))
        template_obj = env.get_template(template)
        html_content = template_obj.render(**params)

        # Attach HTML content
        msg.attach(MIMEText(html_content, 'html'))

        # Create a fresh SMTP connection for each request
        server = smtplib.SMTP(smtp_host, smtp_port)
        server.starttls()

        # Login if credentials are provided
        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)

        # Send email
        server.send_message(msg)
        server.quit()

        logger.debug("send_email:: Email sent successfully",
                    subject=subject,
                    receivers=receivers,
                    template=template)
        return True

    except Exception as e:
        # Log detailed error information
        logger.error("send_email:: Email could not be sent",
                    error_type=type(e).__name__,
                    error_message=str(e),
                    traceback=traceback.format_exc(),
                    mail_subject=subject,
                    mail_receivers=receivers,
                    mail_template=template)
        return False
