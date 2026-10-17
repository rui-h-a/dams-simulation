#!/bin/bash
# VM startup script for one R11 study batch. All study parameters come from
# instance metadata, so the script contains no private configuration:
#   dams-commit   fixed source commit of rui-h-a/dams-simulation
#   dams-args     arguments passed to ./run.sh after --spec enterprise-r11
#   dams-bucket   results bucket; dams-prefix  object prefix
# The attached service account needs only object create/get on that bucket.
# Each validated arm is uploaded as soon as its receipt exists; the VM powers
# off when the batch ends. A server-side maximum run duration is set at creation.
exec > >(tee -a /var/log/dams-r11.log /dev/ttyS0) 2>&1
md() { curl -sf -H 'Metadata-Flavor: Google' "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"; }
COMMIT=$(md dams-commit); ARGS=$(md dams-args); BUCKET=$(md dams-bucket); PREFIX=$(md dams-prefix)
echo "DAMSR11 start $(date -u +%FT%TZ) $(uname -m) $(nproc) commit=$COMMIT"
apt-get -qq update >/dev/null 2>&1; apt-get -qq -y install git curl >/dev/null 2>&1
mkdir -p /srv && cd /srv
if [ -d /srv/sim/.git ]; then cd sim && [ "$(git rev-parse HEAD)" = "$COMMIT" ] || { echo "DAMSR11 resume-commit-mismatch"; shutdown -h now; }; echo "DAMSR11 resume existing checkout"
else git clone -q https://github.com/rui-h-a/dams-simulation.git sim && cd sim && git checkout -q "$COMMIT" || { echo "DAMSR11 checkout-failed"; shutdown -h now; }; fi
OUT=/srv/runs/batch
token() { curl -sf -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])'; }
put() {  # put LOCAL OBJECT_NAME ; create-only (ifGenerationMatch=0), never overwrites
  local name; name=$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))' "$2")
  curl -sf -X POST --data-binary @"$1" -H "Authorization: Bearer $(token)" -H 'Content-Type: application/octet-stream' \
    "https://storage.googleapis.com/upload/storage/v1/b/$BUCKET/o?uploadType=media&ifGenerationMatch=0&name=$name" >/dev/null
}
upload_ready() {
  [ -d "$OUT" ] || return 0
  for f in "$OUT"/plan.json "$OUT"/events.jsonl; do [ -f "$f" ] && cp "$f" "$f.snap" && put "$f.snap" "$PREFIX/$(basename "$f").$(date -u +%Y%m%dT%H%M%SZ)"; done
  find "$OUT" -name arm.json -o -name prehistory.json -o -name failure.json | while read -r r; do
    d=$(dirname "$r"); rel=${d#"$OUT"/}; marker="$d/.uploaded-$(basename "$r")"
    [ -f "$marker" ] && continue
    ok=1
    for f in "$d"/*.json "$d"/*.csv "$d"/*.compact.sqlite; do
      [ -f "$f" ] || continue
      put "$f" "$PREFIX/$rel/$(basename "$f")" || ok=0
    done
    [ $ok = 1 ] && touch "$marker" && echo "DAMSR11 uploaded $rel/$(basename "$r")"
  done
}
( while true; do sleep 300; upload_ready; done ) &
UPLOADER=$!
# shellcheck disable=SC2086
HOME=/root ./run.sh --spec enterprise-r11 $ARGS --output "$OUT"; status=$?
echo "DAMSR11 batch exit $status $(date -u +%FT%TZ)"
kill $UPLOADER 2>/dev/null; upload_ready
[ -f "$OUT/paired-outcomes.json" ] && put "$OUT/paired-outcomes.json" "$PREFIX/paired-outcomes.json"
echo "{\"exit\":$status,\"commit\":\"$COMMIT\",\"utc\":\"$(date -u +%FT%TZ)\"}" > /srv/done.json && put /srv/done.json "$PREFIX/done.json"
put /var/log/dams-r11.log "$PREFIX/vm.log"
echo "DAMSR11 done"
shutdown -h now
