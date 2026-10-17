"""Random tokens for private links and invitations.

Tokens are 32 random bytes, so a plain SHA-256 hash is enough to store
them safely: there's nothing to guess, unlike a password.
"""

import hashlib
import secrets


def new_token():
    """A new token and its hash. Send the token; store only the hash."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()
