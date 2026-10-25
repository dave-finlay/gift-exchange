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
It's early days, but the main flow works: organizers set up a gift
exchange, everyone gets an email with a private link, and the draw is
made and sent. A container image is built and scanned for
vulnerabilities; backups, withdrawals and reminders are next.

See [docs/SELF-HOSTING.md](docs/SELF-HOSTING.md) to run your own site, and
[docs/DESIGN.md](docs/DESIGN.md) for the design.

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
# Emails aren't sent in development: each is saved as a .eml file in
# data/emails, which opens in any mail app. (For a real test send, put the
# email provider's secret files in a folder and set SECRETS_DIR to it.)

# To try pages on a phone on the same Wi-Fi, listen on the local network,
# then open http://<your-computer>.local:8000 or http://<its address>:8000
DJANGO_DEBUG=1 uv run python manage.py runserver 0.0.0.0:8000
```

### The container image

The `Dockerfile` builds the image the server runs: the app under
gunicorn, as an unprivileged user, serving its own static files. It runs
`deploy/start.sh`, which brings the database up to date and starts
gunicorn. CI (`.github/workflows/image.yml`) builds it on every pull
request, checks that it starts and serves pages, and scans it with
[grype](https://github.com/anchore/grype): a fixable high or critical
vulnerability fails the build, and every finding goes to the repository's
Security tab. On `main`, and daily, it's published as
`ghcr.io/<owner>/gift-exchange`.

To try it locally (with [OrbStack](https://orbstack.dev) or Docker), with
throwaway secrets:

```sh
docker build -t gift-exchange .
mkdir -p /tmp/ge-secrets
uv run python -c "import secrets; print(secrets.token_urlsafe(50))" > /tmp/ge-secrets/django_secret_key
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > /tmp/ge-secrets/assignment_keys
echo not-a-real-key > /tmp/ge-secrets/mailjet_api_key
echo not-a-real-key > /tmp/ge-secrets/mailjet_secret_key
docker run --rm -p 8000:8000 -e SITE_DOMAIN=localhost \
  -v /tmp/ge-secrets:/run/secrets:ro gift-exchange
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
| `Dockerfile`, `deploy/` | The container image, and the files for running it on a server: Compose, Caddy, a site check and the secrets script |
| `tests/` | Tests, including property-based tests with Hypothesis |
| `docs/DESIGN.md` | Architecture, data model and screens |
| `docs/SELF-HOSTING.md` | Running your own site |

## License

[MIT](LICENSE)
