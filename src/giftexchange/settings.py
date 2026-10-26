"""Django settings.

Everything specific to one installation comes from configuration (see
"Configuration" in docs/DESIGN.md):

- Site settings come from environment variables, which Docker Compose
  fills from /etc/gift-exchange/site.env.
- Secrets come from files in SECRETS_DIR (/run/secrets in the
  containers), one value per line, newest first. Older lines are kept
  only while keys are being rotated.

With DJANGO_DEBUG=1 (development and tests), missing secret files fall
back to fixed development values. Without it they're an error.
"""

import base64
import hashlib
import os
import pkgutil
import re
import socket
from pathlib import Path

import anymail.backends
from django.core.exceptions import ImproperlyConfigured


def env(name, default=None):
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if default is None:
        raise ImproperlyConfigured(f"Set {name} in site.env")
    return default


def env_int(name, default):
    return int(env(name, str(default)))


def env_list(name):
    """Items separated by spaces or commas. (Caddy, which reads the same
    site.env, needs spaces.)"""
    return [item for item in re.split(r"[\s,]+", env(name, "")) if item]


def local_network_address():
    """This computer's address on the local network, so a phone on the same
    Wi-Fi can reach the development server. Connecting a UDP socket sends
    nothing; it only picks the network interface."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # A documentation address
            return s.getsockname()[0]
    except OSError:
        return None


DEBUG = env("DJANGO_DEBUG", "0") == "1"

SECRETS_DIR = Path(env("SECRETS_DIR", "/run/secrets"))


def secret_lines(name, dev_value):
    """The non-blank lines of a secret file, newest first."""
    try:
        text = (SECRETS_DIR / name).read_text()
    except FileNotFoundError:
        if DEBUG:
            return [dev_value]
        raise ImproperlyConfigured(
            f"Missing secret file {SECRETS_DIR / name}"
        ) from None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ImproperlyConfigured(f"Secret file {SECRETS_DIR / name} is empty")
    return lines


def dev_value(label):
    return base64.urlsafe_b64encode(hashlib.sha256(label.encode()).digest()).decode()


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(env("DATA_DIR", str(BASE_DIR.parents[1] / "data")))
if DEBUG:
    DATA_DIR.mkdir(exist_ok=True)  # In production, it's a volume

# Site

SITE_DOMAIN = env("SITE_DOMAIN", "localhost" if DEBUG else None)
SITE_NAME = env("SITE_NAME", "Gift Exchange")
EMAIL_FROM = env("EMAIL_FROM", f"{SITE_NAME} <hello@{SITE_DOMAIN}>")
DEFAULT_FROM_EMAIL = EMAIL_FROM
OPERATOR_NAME = env("OPERATOR_NAME", "")
OPERATOR_EMAIL = env("OPERATOR_EMAIL", "")

# Requests for any other address are refused. Links are built from the
# address a request came in on, so a site can answer to several domains
# (EXTRA_DOMAINS), each giving links on its own domain.
SITE_DOMAINS = [SITE_DOMAIN, *env_list("EXTRA_DOMAINS")]
ALLOWED_HOSTS = list(SITE_DOMAINS)
if DEBUG:
    # Development: this computer by any name, including its .local name
    # and its address on the local network, for trying pages on a phone.
    ALLOWED_HOSTS += ["localhost", "127.0.0.1", "[::1]", ".local"]
    if address := local_network_address():
        ALLOWED_HOSTS.append(address)
CSRF_TRUSTED_ORIGINS = [f"https://{domain}" for domain in SITE_DOMAINS]

# Size limits

MAX_CIRCLES_PER_USER = env_int("MAX_CIRCLES_PER_USER", 5)
MAX_PEOPLE_PER_CIRCLE = env_int("MAX_PEOPLE_PER_CIRCLE", 50)
MAX_EXCHANGES_PER_CIRCLE_PER_YEAR = env_int("MAX_EXCHANGES_PER_CIRCLE_PER_YEAR", 4)
# Gift exchanges that haven't ended yet, including drafts.
MAX_EXCHANGES_PER_USER = env_int("MAX_EXCHANGES_PER_USER", 10)
MAX_EMAILS_PER_EXCHANGE_PER_DAY = env_int("MAX_EMAILS_PER_EXCHANGE_PER_DAY", 150)

# Secrets

SECRET_KEY, *SECRET_KEY_FALLBACKS = secret_lines(
    "django_secret_key", dev_value("giftexchange-dev-secret-key")
)

# Fernet keys for encrypting assignments, newest first.
ASSIGNMENT_KEYS = secret_lines(
    "assignment_keys", dev_value("giftexchange-dev-assignment-key")
)

# Application

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "axes",
    "anymail",
    "giftexchange.accounts",
    "giftexchange.exchanges",
    "giftexchange.audit",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Static files, before anything that needs a session or database.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "giftexchange.http.security_headers",
    # Last, as django-axes requires.
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "giftexchange.urls"
WSGI_APPLICATION = "giftexchange.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "giftexchange.http.site",
            ],
        },
    },
]

# Database: SQLite in WAL mode. IMMEDIATE transactions take the write lock
# at the start, so concurrent writers wait (up to the timeout) instead of
# failing part-way through.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
            "timeout": 5,
            "init_command": (
                "PRAGMA journal_mode=WAL;"
                "PRAGMA synchronous=NORMAL;"
                "PRAGMA foreign_keys=ON;"
            ),
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Sign-in

AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "signin"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "signin"

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Lockout after repeated failed sign-ins (django-axes): 5 failures from one
# IP address, or for one email address, lock it out for an hour.
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1
AXES_LOCKOUT_PARAMETERS = ["ip_address", "username"]
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = "accounts/locked_out.html"
AXES_CLIENT_IP_CALLABLE = "giftexchange.http.client_ip"
AXES_USERNAME_FORM_FIELD = "username"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation."
        "UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

INVITATION_DAYS = env_int("INVITATION_DAYS", 7)

# Email, sent over the provider's HTTPS API by django-anymail.
# EMAIL_PROVIDER names any provider Anymail supports. Its settings come
# from secret files named after Anymail's settings in lower case:
# mailjet_api_key for MAILJET_API_KEY, and so on. emails.py turns off link
# and open tracking on every message, and Anymail refuses to send through
# a provider that can't do that per message.
EMAIL_PROVIDERS = sorted(
    module.name
    for module in pkgutil.iter_modules(anymail.backends.__path__)
    if module.name not in {"base", "base_requests", "console", "test"}
)
EMAIL_PROVIDER = env("EMAIL_PROVIDER", "mailjet")
if EMAIL_PROVIDER not in EMAIL_PROVIDERS:
    raise ImproperlyConfigured(
        f"EMAIL_PROVIDER must be one of: {', '.join(EMAIL_PROVIDERS)}"
    )
_secrets = sorted(SECRETS_DIR.glob(f"{EMAIL_PROVIDER}_*"))

# Development sends real email only once the provider's secret files are
# installed (for a test send). Until then, each email is saved as a .eml
# file in data/emails, which opens in any mail app.
SERVER_EMAIL = EMAIL_FROM
if _secrets or not DEBUG:
    if not _secrets:
        raise ImproperlyConfigured(
            f"No secret files for {EMAIL_PROVIDER} in {SECRETS_DIR}"
            f" (such as {EMAIL_PROVIDER}_api_key)"
        )
    EMAIL_BACKEND = f"anymail.backends.{EMAIL_PROVIDER}.EmailBackend"
    ANYMAIL = {f.name.upper(): secret_lines(f.name, "")[0] for f in _secrets}
else:
    EMAIL_BACKEND = "giftexchange.mail.EmlFileBackend"
    EMAIL_FILE_PATH = DATA_DIR / "emails"
    ANYMAIL = {}

# What happened to each email (delivered, bounced, blocked, marked as
# spam) comes back from the provider to /anymail/<provider>/tracking/, once
# this secret is installed: "user:password", which the provider sends
# with each request (it's in the address given to the provider). Without
# it, that address doesn't exist.
if (SECRETS_DIR / "anymail_webhook_secret").exists():
    ANYMAIL["WEBHOOK_SECRET"] = secret_lines("anymail_webhook_secret", "")

# Internationalization

LANGUAGE_CODE = "en-ie"
TIME_ZONE = env("TIME_ZONE", "Europe/Dublin")
USE_I18N = True
USE_TZ = True

# Static files

# The app serves its own static files (WhiteNoise), collected into the
# image when it's built, with a hash of their contents in their names, so
# browsers can cache them for good. With DEBUG on, they're served from
# the source folders under their plain names.
STATIC_URL = "static/"
STATIC_ROOT = Path(env("STATIC_ROOT", str(DATA_DIR / "static")))
STATICFILES_DIRS = [BASE_DIR / "static"]
if DEBUG:
    STATIC_ROOT.mkdir(parents=True, exist_ok=True)  # Else WhiteNoise warns
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# Security. Caddy terminates HTTPS, redirects plain HTTP and sets HSTS; it
# tells Django the original scheme in X-Forwarded-Proto.

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Caddy replaces X-Forwarded-For with the real client address, so it can be
# trusted in production. In development nothing sets it.
TRUST_X_FORWARDED_FOR = env("TRUST_X_FORWARDED_FOR", "0" if DEBUG else "1") == "1"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
# Never send our URLs (which may hold private tokens) to other sites.
# Not "no-referrer": browsers then send "Origin: null" on form posts,
# which fails Django's CSRF check.
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Caddy, not Django, handles HSTS (W004) and the redirect to HTTPS (W008).
SILENCED_SYSTEM_CHECKS = ["security.W004", "security.W008"]
