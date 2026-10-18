"""A development email backend: each message becomes a .eml file, which
macOS Mail (or any mail app) opens, HTML and all."""

from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend


class EmlFileBackend(BaseEmailBackend):
    def send_messages(self, messages):
        folder = Path(settings.EMAIL_FILE_PATH)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        for number, message in enumerate(messages):
            (folder / f"{stamp}-{number}.eml").write_bytes(message.message().as_bytes())
        return len(messages)
