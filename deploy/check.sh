#!/bin/sh
# Checks a running site from the machine it runs on, in this folder:
#
#   ./check.sh <site domain>
#
# Run after starting or upgrading it, and by CI. It checks HTTPS (with a
# real certificate, except for localhost), the redirect from plain HTTP,
# security headers, that the admin site isn't public, static files, that
# Caddy's log leaves out private links, and that a client can't choose
# the address used for sign-in lockouts by sending its own
# X-Forwarded-For.
set -eu
domain=$1
tls="--resolve $domain:443:127.0.0.1"
[ "$domain" = localhost ] && tls="--insecure" # Caddy's own certificate
site="https://$domain"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

# shellcheck disable=SC2086 # $tls is two words, or one
get() { curl -sS $tls "$@"; }

location=$(curl -sS -o /dev/null -w '%{redirect_url}' --resolve "$domain:80:127.0.0.1" "http://$domain/signin/")
[ "$location" = "$site/signin/" ] || fail "plain HTTP doesn't redirect to HTTPS ($location)"

get -D "$work/headers" -o "$work/page" "$site/signin/" || fail "no HTTPS"
grep -q '^HTTP/[0-9.]* 200' "$work/headers" || fail "sign-in page: $(head -1 "$work/headers")"
grep -q 'Sign in' "$work/page" || fail "sign-in page has no sign-in form"
grep -qi '^strict-transport-security: max-age=' "$work/headers" || fail "no HSTS header"
grep -qi "^content-security-policy: default-src 'none'" "$work/headers" || fail "no CSP header"
! grep -qi '^server:' "$work/headers" || fail "Server header present"

code=$(get -o /dev/null -w '%{http_code}' "$site/admin/")
[ "$code" = 404 ] || fail "the admin site is public ($code)"

css=$(grep -o '/static/site\.[0-9a-f]*\.css' "$work/page") || fail "no stylesheet link"
get -fo /dev/null "$site$css" || fail "static file $css"

token="check-$(date +%s)"
get -o /dev/null -H "Referer: $site/p/$token/" "$site/p/$token/"
sleep 1
! docker compose logs caddy 2>&1 | grep -q "$token" || fail "Caddy's log holds a private link"

# A failed sign-in claiming to come from 203.0.113.7 (a documentation
# address) must be recorded against the address Caddy saw instead.
user="check@example.invalid"
get -c "$work/jar" -o "$work/form" "$site/signin/"
csrf=$(sed -n 's/.*name="csrfmiddlewaretoken" value="\([^"]*\)".*/\1/p' "$work/form")
get -b "$work/jar" -o /dev/null -H "Referer: $site/signin/" -H "X-Forwarded-For: 203.0.113.7" \
    --data-urlencode "csrfmiddlewaretoken=$csrf" --data-urlencode "username=$user" \
    --data-urlencode "password=not-the-password" "$site/signin/"
ips=$(docker compose exec -T app django-admin shell -c "
from axes.models import AccessAttempt
print(*AccessAttempt.objects.filter(username='$user').values_list('ip_address', flat=True))")
docker compose exec -T app django-admin axes_reset_username "$user" > /dev/null
[ -n "$ips" ] || fail "the failed sign-in wasn't recorded"
case "$ips" in *203.0.113.7*) fail "a client chose its own address ($ips)" ;; esac

echo "OK: $site"
