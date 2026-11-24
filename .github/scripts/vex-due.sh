#!/usr/bin/env bash
# Lists, as a JSON array, the releases on the vex branch whose VEX
# document isn't attested to their image yet (.github/workflows/vex.yml):
#
#   .github/scripts/vex-due.sh <a checkout of the vex branch> [all]
#
# Only releases changed in the last 8 days, unless "all" is given (after
# the workflow hasn't run for a while, say). Nothing here is trusted:
# check-vex.sh checks each release before it's attested.
set -euo pipefail

vex=$1
all=${2:-}
repo=${GITHUB_REPOSITORY:-dave-finlay/gift-exchange}
openvex=https://openvex.dev/ns/v0.2.0

# A JSON document's sha256, with its keys sorted and no spaces.
canonical() {
  jq -S -c . | if command -v sha256sum > /dev/null; then sha256sum; else shasum -a 256; fi | cut -d' ' -f1
}

for folder in "$vex"/releases/*/; do
  [ -d "$folder" ] || continue
  name=$(basename "$folder")
  if ! [[ $name =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "skipping $name: not a release's name" >&2
    continue
  fi
  if [ "$all" != all ] &&
    [ -z "$(git -C "$vex" log -1 --since="8 days ago" --format=%H -- "releases/$name")" ]; then
    continue
  fi
  digest=$(jq -r .dsseEnvelope.payload "$folder/basis.sigstore.json" | base64 -d | jq -r .predicate.image.digest)
  if ! [[ $digest =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "skipping $name: its image digest is $digest" >&2
    continue
  fi
  # Attested already if one of the image's VEX attestations has this
  # document as its predicate.
  want=$(canonical < "$folder/vex.openvex.json")
  attested=$(
    gh api --paginate "repos/$repo/attestations/$digest?predicate_type=$openvex" \
      -q '.attestations[].bundle.dsseEnvelope.payload' 2> /dev/null |
      while read -r payload; do
        echo "$payload" | base64 -d | jq .predicate | canonical
      done || true
  )
  if grep -qx "$want" <<< "$attested"; then
    continue
  fi
  echo "$name"
done | jq -R . | jq -s -c .
