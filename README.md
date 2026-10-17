# gift-exchange

A small, invite-only web app for running gift exchanges ("Secret Santa",
"Kris Kindle"): everyone in a group is secretly assigned one other person
to buy a gift for. It's also the vehicle for an experiment in automated
security: dependency updates, CVE scanning, rebuilds and redeploys.

It's meant to be self-hosted. If you're technically minded, you can fork
this repository and run your own private gift-exchange site for your
family, friends or community: one small server, a few euros a month, no
ads, and your group's data stays on your own server. Then change it to
suit your group.
It's early days: the draw, the data model, sign-in, invitations,
circles and exchange setup (with a "check the rules" test mode) work;
emails and sending the draw are next.

See [docs/DESIGN.md](docs/DESIGN.md) for the design.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync                    # create .venv and install dependencies
uv run pytest              # run the tests
uv run ruff check .        # lint
uv run ruff format .       # format

# Run the site locally, with development secrets and a database in data/
DJANGO_DEBUG=1 uv run python manage.py migrate
DJANGO_DEBUG=1 uv run python manage.py createsuperuser
DJANGO_DEBUG=1 uv run python manage.py runserver

# To try pages on a phone on the same Wi-Fi, listen on the local network,
# then open http://<your-computer>.local:8000 or http://<its address>:8000
DJANGO_DEBUG=1 uv run python manage.py runserver 0.0.0.0:8000
```

## Layout

| Path | What's in it |
|---|---|
| `src/giftexchange/matching.py` | The draw: fair random assignments under the exchange's rules, feasibility explanations, test-mode statistics and repairs. Pure Python with no dependencies. |
| `src/giftexchange/settings.py` | Django settings. Site values come from environment variables and secrets from files; see "Configuration" in the design. |
| `src/giftexchange/accounts/` | Organizers, sign-in and invitations |
| `src/giftexchange/http.py` | Security headers (a strict Content-Security-Policy, `noindex`) and the client's IP address behind Caddy |
| `src/giftexchange/templates/`, `static/` | The base page layout and stylesheet; each app has its own templates |
| `src/giftexchange/exchanges/` | Circles, people, rules, exchanges and participants (`models.py`); the organizer pages (`views.py`, `forms.py`); turning rules into a draw and saving it (`draws.py`); test mode (`checks.py`); encrypting assignments (`secrecy.py`) |
| `src/giftexchange/audit/` | A log of security-relevant actions |
| `tests/` | Tests, including property-based tests with Hypothesis |
| `docs/DESIGN.md` | Architecture, data model and screens |

## License

[MIT](LICENSE)
