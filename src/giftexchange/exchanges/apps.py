from django.apps import AppConfig


class ExchangesConfig(AppConfig):
    name = "giftexchange.exchanges"

    def ready(self):
        from . import emails  # noqa: F401 (connects record_outcome)
