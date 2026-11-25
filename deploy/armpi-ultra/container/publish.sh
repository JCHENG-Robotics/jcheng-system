#!/usr/bin/env bash
# Push the local image to a container registry and record its manifest digest.
#
# The 7.1 GB image is not stored in git. Push it from wherever the upload is
# cheapest; the robot's Wi-Fi is the slow path. Run `docker login <registry>`
# first — this script does not handle credentials.
#
# Usage:
#   ./publish.sh ghcr.io/jcheng-robotics/armpi-ultra:20261125
#   ./publish.sh ghcr.io/jcheng-robotics/armpi-ultra:20261125 --update-lock
#
# --update-lock rewrites the `digest:` line in image.lock. Without it the digest
# is only printed.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$#" -lt 1 ]; then
  echo "usage: publish.sh <registry-ref-with-tag> [--update-lock]" >&2
  exit 2
fi
REF="$1"
UPDATE_LOCK=0
[ "${2:-}" = "--update-lock" ] && UPDATE_LOCK=1

LOCAL="$(awk '/^tag:[[:space:]]/{print $2}' "$HERE/image.lock")"
[ -n "$LOCAL" ] || { echo "ERROR: image.lock has no tag:" >&2; exit 2; }
docker image inspect "$LOCAL" >/dev/null 2>&1 || {
  echo "ERROR: local image $LOCAL not found on this host" >&2
  echo "       the image lives on the robot; push from there or transfer it first" >&2
  exit 2
}

REPO="${REF%:*}"
[[ "$REF" == *:* ]] || { echo "ERROR: include an explicit tag in $REF" >&2; exit 2; }

echo "tagging $LOCAL -> $REF"
docker tag "$LOCAL" "$REF"

echo "pushing $REF (7.1 GB; this can take a while)"
docker push "$REF"

DIGEST_LINE="$(docker image inspect -f '{{range .RepoDigests}}{{println .}}{{end}}' "$REF" | grep "^${REPO}@" | head -1 || true)"
[ -n "$DIGEST_LINE" ] || { echo "ERROR: no RepoDigest for $REPO after push" >&2; exit 1; }

echo
echo "manifest: $DIGEST_LINE"

if [ "$UPDATE_LOCK" = "1" ]; then
  tmp="$(mktemp)"
  if grep -q '^digest:' "$HERE/image.lock"; then
    awk -v d="$DIGEST_LINE" '/^digest:[[:space:]]/{print "digest: " d; next} {print}' \
      "$HERE/image.lock" > "$tmp"
  else
    cat "$HERE/image.lock" > "$tmp"
    printf 'digest: %s\n' "$DIGEST_LINE" >> "$tmp"
  fi
  mv "$tmp" "$HERE/image.lock"
  echo "image.lock updated"
else
  echo "record it with:  ./publish.sh $REF --update-lock   (after the push succeeds)"
  echo "or add this line to image.lock:  digest: $DIGEST_LINE"
fi
