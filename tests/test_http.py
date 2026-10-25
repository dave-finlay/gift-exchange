from django.test import RequestFactory

from giftexchange.http import client_ip


def request(**meta):
    return RequestFactory().get("/", REMOTE_ADDR="192.0.2.1", **meta)


def test_behind_caddy_the_address_is_the_one_caddy_saw(settings):
    # Caddy replaces the header with the address it saw; if anything ever
    # appends to it instead, the last entry is still Caddy's.
    settings.TRUST_X_FORWARDED_FOR = True
    assert client_ip(request(HTTP_X_FORWARDED_FOR="198.51.100.2")) == "198.51.100.2"
    spoofed = request(HTTP_X_FORWARDED_FOR="203.0.113.7, 198.51.100.2")
    assert client_ip(spoofed) == "198.51.100.2"
    assert client_ip(request()) == "192.0.2.1"


def test_without_a_proxy_the_header_is_ignored(settings):
    settings.TRUST_X_FORWARDED_FOR = False
    assert client_ip(request(HTTP_X_FORWARDED_FOR="203.0.113.7")) == "192.0.2.1"
