"""Encrypting and reading assignments.

This is the only module that decrypts receivers, and only two places may
call it: the participant page (showing someone their own assignment) and
draws.py (avoiding recent pairings, and repairs). Neither shows the result
to an organiser. Organiser pages and the admin site never decrypt;
tests/test_secrecy.py checks who imports this module.
"""

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings


def _fernet():
    # The first key encrypts; all of them decrypt, so old keys keep working
    # while keys are rotated.
    return MultiFernet([Fernet(key) for key in settings.ASSIGNMENT_KEYS])


def seal(giver, receiver):
    """The ciphertext for an Assignment from giver to receiver (both
    Participants). The IDs inside tie it to its row."""
    plaintext = f"{giver.exchange_id}:{giver.pk}:{receiver.pk}"
    return _fernet().encrypt(plaintext.encode()).decode()


def receiver_id(assignment):
    """The receiving Participant's ID. Fails if the ciphertext was copied
    from another row."""
    plaintext = _fernet().decrypt(assignment.receiver_ciphertext.encode()).decode()
    exchange_id, giver_id, receiver = (int(part) for part in plaintext.split(":"))
    giver = assignment.giver
    if (exchange_id, giver_id) != (giver.exchange_id, giver.pk):
        raise ValueError("This assignment's ciphertext belongs to another row")
    return receiver
