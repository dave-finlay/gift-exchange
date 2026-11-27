#!/usr/bin/env bash
# Checks one release's VEX document from Securomatic before it's attested to
# the image (.github/workflows/vex.yml), and prints the image's digest:
#
#   .github/scripts/check-vex.sh <the release's folder on the vex branch>
#
# It checks that:
# - Securomatic signed the release's basis record, with one of the keys in
#   .github/securomatic-keys/ (read from main: Securomatic can write to the vex
#   branch, so a key there proves nothing);
# - the record is the basis of this VEX document, and names an image of
#   this repo's;
# - every statement in the document is about that image;
# - this repo built that image from the commit the record names: one of
#   its own signed attestations for the image (image.yml's build
#   provenance for main's builds, release.yml's SBOM for a version) says
#   so.
#
# The files are only read: nothing from the vex branch is run. See
# https://securomatic.dev/basis/v1
set -euo pipefail

dir=$1
repo=${GITHUB_REPOSITORY:-dave-finlay/gift-exchange}
image=ghcr.io/$repo
keys=${SECUROMATIC_KEYS:-.github/securomatic-keys}
bundle=$dir/basis.sigstore.json
vex=$dir/vex.openvex.json
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

fail() {
  echo "$dir: $*" >&2
  exit 1
}
sha256() {
  if command -v sha256sum > /dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1
}

type=$(jq -r .dsseEnvelope.payloadType "$bundle")
[ "$type" = application/vnd.in-toto+json ] || fail "the basis record's payload is $type"
payload=$work/payload.json
jq -r .dsseEnvelope.payload "$bundle" | base64 -d > "$payload"
# What DSSE signs: "DSSEv1 <len(type)> <type> <len(payload)> <payload>"
{
  printf 'DSSEv1 %d %s %d ' "${#type}" "$type" "$(wc -c < "$payload" | tr -d ' ')"
  cat "$payload"
} > "$work/signed"
signer=
for sig in $(jq -r '.dsseEnvelope.signatures[].sig' "$bundle"); do
  echo "$sig" | base64 -d > "$work/sig"
  for key in "$keys"/*.pem; do
    if openssl dgst -sha256 -verify "$key" -signature "$work/sig" "$work/signed" > /dev/null 2>&1; then
      signer=$key
    fi
  done
done
[ -n "$signer" ] || fail "the basis record isn't signed by any key in $keys"

# Records made before Securomatic was renamed (until November 2026) have
# autosec's predicate type; take it until both releases' are re-signed.
case $(jq -r .predicateType "$payload") in
  https://securomatic.dev/basis/v1) ;;
  https://github.com/dave-finlay/autosec-public/blob/main/basis/v1.md) ;;
  *) fail "it isn't a basis record" ;;
esac
subject=$(jq -r '.subject[] | select(.name == "vex.openvex.json") | .digest.sha256' "$payload")
[ "$subject" = "$(sha256 "$vex")" ] || fail "the VEX document isn't its basis record's subject"
named=$(jq -r .predicate.image.repo "$payload")
[ "$named" = "$image" ] || fail "its basis record names $named, not $image"
digest=$(jq -r .predicate.image.digest "$payload")
[[ $digest =~ ^sha256:[0-9a-f]{64}$ ]] || fail "its image digest is $digest"
commit=$(jq -r .predicate.release.commit "$payload")
[[ $commit =~ ^[0-9a-f]{40}$ ]] || fail "its commit is $commit"

# VEX names the image as a purl, its digest's colon percent-encoded.
jq -e --arg at "@${digest/:/%3A}" --arg repo "repository_url=$image" '
  [.statements[].products[]."@id"]
  | length > 0 and all(contains($at) and contains($repo))
' "$vex" > /dev/null || fail "not every statement is about $image@$digest"

built=$(
  for predicate in https://slsa.dev/provenance/v1 https://spdx.dev/Document/v2.3; do
    gh attestation verify "oci://$image@$digest" --repo "$repo" \
      --predicate-type "$predicate" --format json 2> /dev/null || true
  done | jq -r --arg workflows "https://github.com/$repo/.github/workflows" '
    .[].verificationResult.signature.certificate
    | select(.buildSignerURI == "\($workflows)/image.yml@refs/heads/main"
        or (.buildSignerURI | startswith("\($workflows)/release.yml@refs/tags/v")))
    | .sourceRepositoryDigest
  '
)
grep -qx "$commit" <<< "$built" ||
  fail "this repo's workflows didn't attest building $image@$digest from $commit"

echo "$dir: signed by $(basename "$signer" .pem), $image@$digest, built from $commit" >&2
echo "$digest"
