from dynaconf import Dynaconf, Validator

settings = Dynaconf(
    envvar_prefix="DOITEZ",
    env_switcher="DOITEZ_ENV",
    envvar="DOITEZ_SETTINGS_FILE",
    settings_files=["/config/custom-settings.toml"],
    environments=True,
    env="default",
)

# Required settings
settings.validators.register(
    # Core settings
    Validator("marketplace_project", must_exist=True, is_type_of=str),
    Validator("audience", must_exist=True, is_type_of=str),
    Validator("auto_approve_entitlements", must_exist=True, is_type_of=bool),

    # Optional settings with better validation
    # Slack integration
    Validator("slack_webhook", default=None, is_type_of=(str, type(None))),

    # Google Pub/Sub integration
    Validator("event_topic", default=None, is_type_of=(str, type(None))),

    # Email configuration
    Validator("email_host", default=None, is_type_of=(str, type(None))),
    Validator("email_port", default=None, is_type_of=(int, type(None))),
    Validator("email_sender", default=None, is_type_of=(str, type(None))),

    # Fix the inconsistent naming - choose one approach
    Validator("email_recipients", default=[], is_type_of=list),
)

# Validate all settings
settings.validators.validate_all()
