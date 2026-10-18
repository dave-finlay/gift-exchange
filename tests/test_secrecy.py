import ast
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from giftexchange.exchanges import secrecy
from giftexchange.exchanges.models import Assignment

SOURCE = Path(secrecy.__file__).parents[1]

# The only modules allowed to decrypt assignments: the draw, and the
# participant's own page. Nothing else.
ALLOWED = {"exchanges/draws.py", "exchanges/participant.py"}


def imports_secrecy(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            names = {alias.name for alias in node.names}
            if "secrecy" in (node.module or "") or "secrecy" in names:
                return True
        if isinstance(node, ast.Import) and any(
            "secrecy" in alias.name for alias in node.names
        ):
            return True
    return False


def test_only_allowed_modules_use_secrecy():
    users = {
        str(path.relative_to(SOURCE))
        for path in SOURCE.rglob("*.py")
        if path.name != "secrecy.py" and imports_secrecy(path)
    }
    assert users == ALLOWED


@pytest.fixture
def pair(build):
    b = build([["Alice"], ["Bob"], ["Carol"]])
    return b, b.participant("Alice"), b.participant("Bob")


def test_round_trip(pair):
    _, alice, bob = pair
    a = Assignment.objects.create(
        giver=alice, receiver_ciphertext=secrecy.seal(alice, bob)
    )
    assert secrecy.receiver_id(a) == bob.pk


def test_ciphertext_copied_to_another_row_is_rejected(pair):
    b, alice, bob = pair
    carol = b.participant("Carol")
    stolen = secrecy.seal(alice, bob)
    a = Assignment.objects.create(giver=carol, receiver_ciphertext=stolen)
    with pytest.raises(ValueError):
        secrecy.receiver_id(a)


def test_old_keys_still_decrypt(pair, settings):
    _, alice, bob = pair
    a = Assignment.objects.create(
        giver=alice, receiver_ciphertext=secrecy.seal(alice, bob)
    )
    settings.ASSIGNMENT_KEYS = [
        Fernet.generate_key().decode(),
        *settings.ASSIGNMENT_KEYS,
    ]
    assert secrecy.receiver_id(a) == bob.pk
