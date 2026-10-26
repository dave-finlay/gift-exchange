# Self-Hosting

How to run your own gift exchange site: one small server running Docker
Compose, with [Caddy](https://caddyserver.com) in front for HTTPS and the
app behind it. It costs a few euros a month for the server, plus a
domain. The design behind it is in [DESIGN.md](DESIGN.md).

Backups aren't part of this yet. Until they are, the database lives only
in a Docker volume on the server, so don't rely on the site for anything
you can't afford to lose.

## What You Need

- **A domain**, with DNS you can edit. These steps use Cloudflare's names
  for things; any DNS provider works.
- **A server:** Linux on 64-bit ARM, since the app's image is built for
  ARM only, with 2 GB of memory or more. A Hetzner Cloud CAX11 is enough.
- **An email provider** that [django-anymail](https://anymail.dev)
  supports and that lets each message turn off link and open tracking.
  [Mailjet](https://www.mailjet.com) (the default, with a free tier),
  Postmark, Mailgun, SparkPost and MailerSend do. Brevo, Amazon SES, Resend
  and Scaleway don't, and the app refuses to send through them.
- **[KeePassXC](https://keepassxc.org)** on your own computer, for the
  secrets, and a copy of this repository.

## 1. Email

These steps are for Mailjet; other providers have their own versions.

1. In Mailjet, add your domain under Account settings → Senders &
   Domains, and validate it with the TXT record Mailjet gives you.
2. Under the domain's **Setup SPF/DKIM Authentication**, add both
   records to your DNS. The DKIM record is a TXT record at
   `mailjet._domainkey`. A domain can have only one SPF record: if you
   already have one, add `include:spf.mailjet.com` to it.
3. Add a DMARC record: TXT at `_dmarc`, `v=DMARC1; p=none`.
4. Wait until Mailjet shows both SPF and DKIM as good.

Mailjet sends from its own bounce address, so SPF can never match your
domain. DMARC passes on DKIM alone: emails sent before Mailjet shows DKIM
as good are signed with Mailjet's key, not yours, and fail DMARC.

The API key pair comes in step 4 below.

## 2. The Server

1. **Create the server:** Debian 13 on ARM, with your SSH key, so that
   `ssh root@<server>` works without a password.
2. **Firewall:** allow incoming TCP 22, 80 and 443, and UDP 443 (for
   HTTP/3). Use your provider's firewall (Hetzner's Cloud Firewall, for
   example) rather than `ufw`: Docker's published ports bypass `ufw`.
3. **Automatic security updates:**
   ```sh
   apt update && apt install -y unattended-upgrades
   ```
4. **Docker,** from Docker's own repository (see
   [Docker's instructions for Debian](https://docs.docker.com/engine/install/debian/)):
   ```sh
   apt install -y ca-certificates curl
   install -m 0755 -d /etc/apt/keyrings
   curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
   echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
     > /etc/apt/sources.list.d/docker.list
   apt update && apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
   ```
5. **DNS:** an A record for your domain with the server's IPv4 address,
   and an AAAA record with its IPv6 address. In Cloudflare, set them to
   "DNS only", so Caddy can get its certificate directly.

## 3. Site Settings

From your copy of the repository, put the Compose files on the server:

```sh
ssh root@<server> mkdir -p /opt/gift-exchange /etc/gift-exchange
scp deploy/compose.yaml deploy/Caddyfile deploy/check.sh root@<server>:/opt/gift-exchange/
scp deploy/site.env.example root@<server>:/etc/gift-exchange/site.env
```

Then fill in `/etc/gift-exchange/site.env` on the server. Each setting is
explained in the file. Nothing in it is secret.

By default the site runs the latest image from `main`. To run a release
instead, which is what most sites should do, put it in
`/opt/gift-exchange/.env`:

```sh
APP_IMAGE=ghcr.io/dave-finlay/gift-exchange:0.9.0
```

Releases are listed on the repository's Releases page, each with its
image's SBOM.

## 4. Secrets

Secrets live in KeePassXC, and a script copies them to the server, into
`/etc/gift-exchange/secrets/app/`, one file per secret.

In your KeePassXC database, make a group `gift-exchange`, with a group
`app` inside it. In that, add one entry per secret, with the title as
the secret's name and the value as its password:

| Entry title | Value |
|---|---|
| `django_secret_key` | A new random value (below) |
| `assignment_keys` | A new encryption key (below) |
| `mailjet_api_key` | Mailjet's API key (Account settings → API Key Management) |
| `mailjet_secret_key` | Mailjet's secret key |

To make the two new values without showing them on screen, copy each to
the clipboard, then paste it into the entry's password field:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(50), end='')" | pbcopy
python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode(), end='')" | pbcopy
```

(`pbcopy` is macOS's; on Linux, `xclip -selection clipboard` does the
same.)

**Keep `assignment_keys` safe.** Assignments are encrypted with it.
Without it, nobody can see who has whom, not even you.

Then install them on the server:

```sh
deploy/install-secrets.sh ~/path/to/your-database.kdbx root@<server>
```

It asks for the database's password, copies each entry in
`gift-exchange/app` to the server, and lists any files on the server that
aren't in KeePassXC, without deleting them. Run it again after changing a
secret, then restart the app (step 5) for it to take effect.

With another email provider, use its Anymail settings' names in lower
case instead of Mailjet's, such as `postmark_server_token`, and set
`EMAIL_PROVIDER` in `site.env`.

## 5. Start

On the server:

```sh
cd /opt/gift-exchange
docker compose up --detach --wait
./check.sh <your domain>
```

`--wait` returns once the app answers. The check tests HTTPS with a real
certificate, the redirect from plain HTTP, security headers, that the
admin site isn't public, and more; it prints `OK` at the end.

Then make your own account, as the site's operator:

```sh
docker compose exec app django-admin createsuperuser
```

It asks for your email address, your name (shown on the emails you send,
as "<name> via Gift Exchange"; you can change it later from the menu)
and a password.

Sign in at `https://<your domain>/signin/`. As the operator, you can
invite other organizers from the menu.

## Running It

- **Upgrade:** for a release, change `APP_IMAGE` in `.env` to the new
  version first (and copy over the release's `compose.yaml` and
  `Caddyfile`, if they've changed). Then:
  ```sh
  cd /opt/gift-exchange
  docker compose pull && docker compose up --detach --wait && ./check.sh <your domain>
  ```
  The app brings its database up to date when it starts.
- **After changing `site.env` or a secret:**
  `docker compose up --detach --wait --force-recreate`.
- **Logs:** `docker compose logs app` or `docker compose logs caddy`.
  Neither holds participants' private links.
- **Admin tasks:** the admin site isn't reachable from outside. Use
  `docker compose exec app django-admin <command>` instead, for example
  `django-admin changepassword <email>`, or `django-admin
  axes_reset_username <email>` to lift a sign-in lockout.
- **Where things are:**

  | What | Where |
  |---|---|
  | Compose files and the check | `/opt/gift-exchange/` |
  | Site settings | `/etc/gift-exchange/site.env` |
  | Secrets | `/etc/gift-exchange/secrets/app/` |
  | The database | The `gift-exchange_app-data` Docker volume |
  | Caddy's certificates | The `gift-exchange_caddy-data` Docker volume |
