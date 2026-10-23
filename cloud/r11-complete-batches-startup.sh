#!/bin/bash
# Complete-retention study VM run in batches of worlds (Root, 2026-10-22). Metadata: dams-commit (exact SHA of
# the enterprise candidate), dams-args, dams-prefix. Runs the candidate's own run.sh
# (new batches keep complete journals, prehistory and checkpoints), resumes from its
# bound plan after a Spot restart, uploads small progress files every 15 minutes and,
# when the batch ends, the complete output directory create-only with a full SHA-256
# manifest, then powers off. Absolute stop: the VM's server-side max-run-duration.
exec > >(tee -a /var/log/dams-r11c.log /dev/ttyS0) 2>&1
md() { curl -sf -H 'Metadata-Flavor: Google' "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"; }
COMMIT=$(md dams-commit); ARGS=$(md dams-args); PREFIX=$(md dams-prefix); B=gs://$(md dams-bucket)
echo "DAMSR11C start $(date -u +%FT%TZ) $(nproc) cpu commit=$COMMIT"
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "DAMSR11C bad-commit"; shutdown -h now; exit 2; }
apt-get -qq update >/dev/null 2>&1; apt-get -qq -y install git curl python3 >/dev/null 2>&1
BUCKET=${B#gs://}
token() { curl -sf -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])'; }
# Create-only upload (the workload identity may create objects but not list/read
# them); the response's MD5 is compared with the local file. Prints OK/DIFF/FAIL.
put() {
  local name resp; name=$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))' "$2")
  resp=$(curl -s -X POST -T "$1" -H "Authorization: Bearer $(token)" -H 'Content-Type: application/octet-stream' \
    "https://storage.googleapis.com/upload/storage/v1/b/$BUCKET/o?uploadType=media&ifGenerationMatch=0&name=$name")
  python3 - "$1" "$resp" <<'PY'
import base64, hashlib, json, sys
try: r = json.loads(sys.argv[2])
except Exception: print('FAIL'); sys.exit(1)
if 'md5Hash' not in r: print('FAIL ' + str(r.get('error', {}).get('code'))); sys.exit(1)
h = hashlib.md5()
with open(sys.argv[1], 'rb') as f:
    for b in iter(lambda: f.read(8 << 20), b''): h.update(b)
ok = base64.b64encode(h.digest()).decode() == r['md5Hash']
print('OK' if ok else 'DIFF'); sys.exit(0 if ok else 1)
PY
}
mkdir -p /srv/runs && cd /srv
if [ -d sim/.git ]; then cd sim; [ "$(git rev-parse HEAD)" = "$COMMIT" ] || { echo "DAMSR11C source-mismatch"; shutdown -h now; exit 2; }
else git clone -q https://github.com/rui-h-a/dams-simulation.git sim && cd sim && git checkout -q "$COMMIT" || { echo "DAMSR11C checkout-failed"; shutdown -h now; exit 2; }; fi
[ -z "$(git status --porcelain)" ] || { echo "DAMSR11C modified-checkout"; shutdown -h now; exit 2; }
# dams-batches: semicolon-separated world lists, e.g. "1000,1001,1002;1003,1004,1005".
# Each batch: run.sh (complete retention; inference admits its raw evidence on this
# machine) -> full SHA-256 manifest -> create-only upload with MD5 check of every
# file -> only if every file verified, the local batch directory is removed and
# the next batch starts. Any failed upload stops the VM with local data kept.
BATCHES=$(md dams-batches); i=0
IFS=';' read -ra LIST <<< "$BATCHES"
for worlds in "${LIST[@]}"; do
  i=$((i+1)); OUT=/srv/runs/batch-$i
  if [ -f /srv/runs/batch-$i.uploaded ]; then echo "DAMSR11C batch $i already uploaded"; continue; fi
  echo "DAMSR11C batch $i worlds $worlds start $(date -u +%FT%TZ)"
  HOME=/root ./run.sh --spec enterprise-r11 $ARGS --worlds $worlds --output $OUT; status=$?
  echo "DAMSR11C batch $i exit $status $(date -u +%FT%TZ) $(du -sb $OUT | cut -f1) bytes"
  python3 - "$OUT" /srv/manifest-$i.tsv <<'PY'
import hashlib, os, sys
root, out = sys.argv[1], open(sys.argv[2], 'w')
out.write('path\tbytes\tsha256\n')
for d, dirs, files in os.walk(root):
    dirs.sort()
    for f in sorted(files):
        p = os.path.join(d, f); h = hashlib.sha256()
        with open(p, 'rb') as s:
            for b in iter(lambda: s.read(8 << 20), b''): h.update(b)
        out.write(f'{os.path.relpath(p, root)}\t{os.path.getsize(p)}\t{h.hexdigest()}\n')
PY
  up=0; nf=0
  while IFS=$'\t' read -r rel bytes sha; do
    [ "$rel" = path ] && continue
    r=$(put "$OUT/$rel" "$PREFIX/batch-$i/$rel"); nf=$((nf+1))
    [ "$r" = OK ] || { echo "DAMSR11C upload $r $rel"; up=1; }
  done < /srv/manifest-$i.tsv
  put /srv/manifest-$i.tsv "$PREFIX/batch-$i/manifest.tsv" >/dev/null
  echo "{\"batch\":$i,\"worlds\":\"$worlds\",\"exit\":$status,\"upload\":$up,\"files\":$nf,\"commit\":\"$COMMIT\",\"utc\":\"$(date -u +%FT%TZ)\"}" > /srv/batch-$i.json
  put /srv/batch-$i.json "$PREFIX/batch-$i/receipt.json" >/dev/null
  if [ "$status" = 0 ] && [ "$up" = 0 ]; then
    touch /srv/runs/batch-$i.uploaded; rm -rf "$OUT"; echo "DAMSR11C batch $i uploaded $nf files; local copy removed"
  else
    echo "DAMSR11C batch $i not cleared (exit $status upload $up); stopping with local data kept"; cp /var/log/dams-r11c.log /srv/vm.log; put /srv/vm.log "$PREFIX/vm.log.$(date -u +%Y%m%dT%H%M%SZ)" >/dev/null; shutdown -h now; exit 1
  fi
done
echo "{\"batches\":$i,\"commit\":\"$COMMIT\",\"utc\":\"$(date -u +%FT%TZ)\"}" > /srv/done.json; put /srv/done.json "$PREFIX/done.json" >/dev/null
cp /var/log/dams-r11c.log /srv/vm.log; put /srv/vm.log "$PREFIX/vm.log.$(date -u +%Y%m%dT%H%M%SZ)" >/dev/null
shutdown -h now
