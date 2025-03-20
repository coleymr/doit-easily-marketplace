""" Module providing config settings """

import traceback
from dynaconf import Dynaconf, Validator

settings = Dynaconf(
    envvar_prefix="DOITEZ",
    env_switcher="DOITEZ_ENV",
    envvar="DOITEZ_SETTINGS_FILE",
    settings_files=["default_settings.toml", "/config/custom-settings.toml"],
    environments=True,
    env="default",
)

# Required settings
settings.validators.register(
    # Core settings
    Validator("marketplace_project", must_exist=True, is_type_of=(str, type(None))),
    Validator("audience", must_exist=True, is_type_of=(str, type(None))),
    Validator("auto_approve_entitlements", must_exist=True, is_type_of=bool),
    # Sendgrid Settings
    Validator(
        "sendgrid_api_key", must_exist=True, default=None, is_type_of=(str, type(None))
    ),
    Validator(
        "sendgrid_from_email",
        must_exist=True,
        default=None,
        is_type_of=(str, type(None)),
    ),
    # Mail Settings
    Validator(
        "email_host", must_exist=True, default=None, is_type_of=(str, type(None))
    ),
    Validator(
        "email_port", must_exist=True, default=None, is_type_of=(int, type(None))
    ),
    Validator(
        "email_sender", must_exist=True, default=None, is_type_of=(str, type(None))
    ),
    # Fix the inconsistent naming - choose one approach
    Validator("email_recipients", must_exist=True, default=[], is_type_of=(list, [])),
    # Optional settings with better validation
    # Slack integration
    Validator("slack_webhook", default=None, is_type_of=(str, type(None))),
    # Google Pub/Sub integration
    Validator("event_topic", default=None, is_type_of=(str, type(None))),
)

# Validate all settings
try:
    settings.validators.validate_all()
except Exception as e:
    traceback.print_exc()
