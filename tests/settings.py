"""Settings for the tests: development secrets, and a fast password hasher."""

import os

os.environ["DJANGO_DEBUG"] = "1"
os.environ["SITE_DOMAIN"] = "example.org"

from giftexchange.settings import *  # noqa: E402, F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
