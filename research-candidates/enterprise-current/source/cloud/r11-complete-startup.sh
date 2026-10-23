#!/bin/bash
# Complete-retention study VM (Root, 2026-10-22). Metadata: dams-commit (exact SHA of
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
OUT=/srv/runs/batch
( while true; do sleep 900; ts=$(date -u +%Y%m%dT%H%M%SZ)
    for f in plan.json events.jsonl; do [ -f $OUT/$f ] && cp $OUT/$f /tmp/up.$f && put /tmp/up.$f "$PREFIX/progress/$f.$ts" >/dev/null; done
    df -B1 --output=used,avail /srv | tail -1 > /tmp/df.txt; put /tmp/df.txt "$PREFIX/progress/df.$ts" >/dev/null
  done ) &
HOME=/root ./run.sh --spec enterprise-r11 $ARGS --output $OUT; status=$?
echo "DAMSR11C batch exit $status $(date -u +%FT%TZ)"
python3 - "$OUT" /srv/manifest.tsv <<'PY'
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
  r=$(put "$OUT/$rel" "$PREFIX/output/$rel"); nf=$((nf+1))
  [ "$r" = OK ] || { echo "DAMSR11C upload $r $rel"; up=1; }
done < /srv/manifest.tsv
echo "DAMSR11C uploaded $nf files status $up $(date -u +%FT%TZ)"
put /srv/manifest.tsv "$PREFIX/manifest.tsv.$(date -u +%Y%m%dT%H%M%SZ)" >/dev/null
cp /var/log/dams-r11c.log /srv/vm.log; put /srv/vm.log "$PREFIX/vm.log.$(date -u +%Y%m%dT%H%M%SZ)" >/dev/null
if [ "$status" = 0 ] && [ "$up" = 0 ]; then
  echo "{\"exit\":$status,\"upload\":$up,\"commit\":\"$COMMIT\",\"utc\":\"$(date -u +%FT%TZ)\"}" > /srv/done.json
  put /srv/done.json "$PREFIX/done.json" >/dev/null
else
  echo "{\"exit\":$status,\"upload\":$up,\"commit\":\"$COMMIT\"}" > /srv/incomplete.json
  put /srv/incomplete.json "$PREFIX/incomplete.$(date -u +%Y%m%dT%H%M%SZ).json" >/dev/null
fi
shutdown -h now
