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
from pathlib import Path

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
SITE_NAME = env("SITE_NAME", "Gift exchange")
EMAIL_FROM = env("EMAIL_FROM", f"{SITE_NAME} <hello@{SITE_DOMAIN}>")
DEFAULT_FROM_EMAIL = EMAIL_FROM
OPERATOR_NAME = env("OPERATOR_NAME", "")
OPERATOR_EMAIL = env("OPERATOR_EMAIL", "")

ALLOWED_HOSTS = [SITE_DOMAIN]
if DEBUG:
    ALLOWED_HOSTS += ["localhost", "127.0.0.1", "[::1]"]
CSRF_TRUSTED_ORIGINS = [f"https://{SITE_DOMAIN}"]

# Size limits

MAX_CIRCLES_PER_USER = env_int("MAX_CIRCLES_PER_USER", 5)
MAX_PEOPLE_PER_CIRCLE = env_int("MAX_PEOPLE_PER_CIRCLE", 50)
MAX_EXCHANGES_PER_CIRCLE_PER_YEAR = env_int("MAX_EXCHANGES_PER_CIRCLE_PER_YEAR", 4)
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
    "giftexchange.accounts",
    "giftexchange.exchanges",
    "giftexchange.audit",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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

# Internationalization

LANGUAGE_CODE = "en-ie"
TIME_ZONE = env("TIME_ZONE", "Europe/Dublin")
USE_I18N = True
USE_TZ = True

# Static files

STATIC_URL = "static/"
STATIC_ROOT = DATA_DIR / "static"
STATICFILES_DIRS = [BASE_DIR / "static"]

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
