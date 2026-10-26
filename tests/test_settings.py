"""Settings that depend on configuration, each loaded afresh from a given
environment."""

import runpy

import pytest
from django.core.exceptions import ImproperlyConfigured

import giftexchange.settings


def load(tmp_path, monkeypatch, debug, secrets=(), **env):
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(exist_ok=True)
    for name in secrets:
        (secrets_dir / name).write_text(f"{name}-value\n")
    env = {
        "DJANGO_DEBUG": "1" if debug else "0",
        "SECRETS_DIR": str(secrets_dir),
        "DATA_DIR": str(tmp_path),
        "SITE_DOMAIN": "example.org",
        "EMAIL_PROVIDER": "",
        **env,
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return runpy.run_path(giftexchange.settings.__file__)


PRODUCTION_SECRETS = ["django_secret_key", "assignment_keys"]
MAILJET_SECRETS = ["mailjet_api_key", "mailjet_secret_key"]


def test_production_sends_with_mailjet_by_default(tmp_path, monkeypatch):
    s = load(tmp_path, monkeypatch, False, PRODUCTION_SECRETS + MAILJET_SECRETS)
    assert s["EMAIL_BACKEND"] == "anymail.backends.mailjet.EmailBackend"
    assert s["ANYMAIL"] == {
        "MAILJET_API_KEY": "mailjet_api_key-value",
        "MAILJET_SECRET_KEY": "mailjet_secret_key-value",
    }


def test_any_anymail_provider(tmp_path, monkeypatch):
    secrets = PRODUCTION_SECRETS + ["mailgun_api_key", "mailgun_sender_domain"]
    s = load(tmp_path, monkeypatch, False, secrets, EMAIL_PROVIDER="mailgun")
    assert s["EMAIL_BACKEND"] == "anymail.backends.mailgun.EmailBackend"
    assert s["ANYMAIL"] == {
        "MAILGUN_API_KEY": "mailgun_api_key-value",
        "MAILGUN_SENDER_DOMAIN": "mailgun_sender_domain-value",
    }


def test_production_needs_the_providers_secrets(tmp_path, monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="No secret files for mailjet"):
        load(tmp_path, monkeypatch, False, PRODUCTION_SECRETS)


@pytest.mark.parametrize("provider", ["nosuchprovider", "test", "console"])
def test_an_unknown_provider_is_an_error(tmp_path, monkeypatch, provider):
    with pytest.raises(ImproperlyConfigured, match="must be one of: amazon_ses,"):
        load(tmp_path, monkeypatch, True, EMAIL_PROVIDER=provider)


def test_development_saves_emails_as_files(tmp_path, monkeypatch):
    s = load(tmp_path, monkeypatch, True)
    assert s["EMAIL_BACKEND"] == "giftexchange.mail.EmlFileBackend"
    assert s["EMAIL_FILE_PATH"] == tmp_path / "emails"


def test_development_sends_once_the_secrets_are_installed(tmp_path, monkeypatch):
    s = load(tmp_path, monkeypatch, True, MAILJET_SECRETS)
    assert s["EMAIL_BACKEND"] == "anymail.backends.mailjet.EmailBackend"


def test_extra_domains_separated_by_spaces_or_commas(tmp_path, monkeypatch):
    extra = "kriskindle.example  other.example,third.example"
    s = load(tmp_path, monkeypatch, True, EXTRA_DOMAINS=extra)
    assert s["SITE_DOMAINS"] == [
        "example.org",
        "kriskindle.example",
        "other.example",
        "third.example",
    ]


def test_the_providers_reports_need_a_secret(tmp_path, monkeypatch):
    assert "WEBHOOK_SECRET" not in load(tmp_path, monkeypatch, True)["ANYMAIL"]
    secrets = PRODUCTION_SECRETS + MAILJET_SECRETS + ["anymail_webhook_secret"]
    s = load(tmp_path, monkeypatch, False, secrets)
    assert s["ANYMAIL"]["WEBHOOK_SECRET"] == ["anymail_webhook_secret-value"]
    assert s["ANYMAIL"]["MAILJET_API_KEY"] == "mailjet_api_key-value"
