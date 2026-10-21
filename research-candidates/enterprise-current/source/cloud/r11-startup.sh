#!/bin/bash
# Candidate for a newly admitted R11 batch. No live/periodic SQLite uploads.
# Metadata: dams-commit (exact SHA), dams-args, dams-archive-config (private
# bounded r11-root-archive-admission-v1 JSON). Root separately admits the VM,
# absolute server stop, disk capacity and cumulative cost. This script creates
# no VM, IAM grant, deletion authority or archive acceptance.
set -u -o pipefail
exec > >(tee -a /var/log/dams-r11.log /dev/ttyS0) 2>&1
md() { curl -sf --max-time 15 -H 'Metadata-Flavor: Google' "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"; }
fail() { echo "DAMSR11 refused: $1; preserve attached disk"; shutdown -h now; exit 2; }
COMMIT=$(md dams-commit) || fail missing-source
ARGS=$(md dams-args) || fail missing-study-args
ADMISSION=$(md dams-archive-config) || fail missing-root-archive-admission
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail invalid-source-sha
apt-get -qq update >/dev/null 2>&1 || fail package-index
apt-get -qq -y install git curl python3 lsof >/dev/null 2>&1 || fail required-tools
mkdir -p /srv/r11-archive /srv/runs || fail directories
chmod 700 /srv/r11-archive
CONFIG=/srv/r11-archive/admission.json
if [ -e "$CONFIG" ]; then
  printf '%s\n' "$ADMISSION" | cmp -s - "$CONFIG" || fail changed-archive-admission
else
  (umask 077; printf '%s\n' "$ADMISSION" > "$CONFIG") || fail archive-admission-write
fi
unset ADMISSION
if [ -d /srv/sim/.git ]; then
  cd /srv/sim || fail checkout-directory
  [ "$(git rev-parse HEAD)" = "$COMMIT" ] || fail resume-source-mismatch
  [ -z "$(git status --porcelain)" ] || fail modified-checkout
else
  git clone -q https://github.com/rui-h-a/dams-simulation.git /srv/sim || fail clone
  cd /srv/sim || fail checkout-directory
  git checkout -q "$COMMIT" || fail fixed-checkout
fi
OUT=/srv/runs/batch
# Validate the exact archive implementation/caps before starting computation.
python3 - "$CONFIG" <<'PY' || fail archive-preflight
import sys
from pathlib import Path
from research_tools.r11_archive import config_file, admitted_state
config = config_file(Path(sys.argv[1]))
if admitted_state(config) != Path('/srv/r11-archive/transport'):
    raise SystemExit('cloud archive admission must use the fixed persistent transport directory')
PY
export DAMS_R11_ARGS="$ARGS"
export DAMS_R11_COMMIT="$COMMIT"
# The launcher owns one process group, waits for it, closes its stdout file and
# records its original identity. A completed local batch is never recomputed to
# retry archival. Historical done.json is not an archive/deletion acceptance.
python3 - "$OUT" /srv/r11-archive <<'PY' || fail owned-runner
import hashlib, json, os, shlex, subprocess, sys, time
from pathlib import Path
from dams_sim.storage import atomic_json, file_digest
from research_tools.r11_archive import wait_pipeline_group_absent
out, state = map(Path, sys.argv[1:])
arguments = shlex.split(os.environ['DAMS_R11_ARGS'])
allowed = {'--scale','--context','--growth','--pre-years','--post-years','--worlds','--arms','--jobs','--variant'}
if len(arguments)%2 or any(a not in allowed for a in arguments[::2]):
    raise SystemExit('study metadata must contain only declared study option/value pairs')
identity = {'commit': os.environ['DAMS_R11_COMMIT'], 'args_sha256': hashlib.sha256(json.dumps(arguments).encode()).hexdigest()}
completed = state/'completed-simulation.json'
if completed.exists():
    previous = json.loads(completed.read_text())
    if any(previous.get(k) != v for k,v in identity.items()) or previous['plan_sha256'] != file_digest(out/'plan.json'):
        raise SystemExit('completed study identity differs; no rerun')
    result = previous
else:
    attempt = str(time.time_ns())
    log = state/('runner-'+attempt+'.log')
    environment = dict(os.environ)
    environment.pop('DAMS_CLOUD_PRIVATE_CONFIG', None)
    with log.open('xb') as stream:
        process = subprocess.Popen(['./run.sh','--spec','enterprise-r11',*arguments,'--output',str(out)],
                                   start_new_session=True, stdout=stream, stderr=subprocess.STDOUT, env=environment)
        code = process.wait()
    admission = json.loads((state/'admission.json').read_text())
    wait_pipeline_group_absent(process.pid, admission['deadline_utc'], max_wait_seconds=5)
    out.mkdir(parents=True, exist_ok=True)
    evidence = out/'execution'; evidence.mkdir(exist_ok=True)
    # The runner has exited and the log is closed; preserve this exact attempt.
    destination = evidence/log.name
    with log.open('rb') as source, destination.open('xb') as target:
        import shutil
        shutil.copyfileobj(source, target)
    result = {**identity, 'owned_pgid': process.pid, 'simulation_exit': code,
              'plan_sha256': file_digest(out/'plan.json') if (out/'plan.json').exists() else None,
              'archive_accepted': False, 'utc_ns': attempt}
    atomic_json(evidence/('simulation-status-'+attempt+'.json'), result)
    if code == 0:
        atomic_json(completed, result)
atomic_json(state/'last-owned-run.json', result)
PY
PGID=$(python3 -c 'import json;print(json.load(open("/srv/r11-archive/last-owned-run.json"))["owned_pgid"])') || fail missing-owned-group
# Only after group absence and lsof closure: all files, including hidden working
# databases, interrupted attempts, original prehistory and source, use the same
# bounded chunk codec. Fixed-generation EOF/SHA verification is mandatory.
.venv/bin/python -m research_tools.r11_archive upload-closed --source "$OUT" \
  --private-config "$CONFIG" --owned-pgid "$PGID" --state /srv/r11-archive/transport
archive_status=$?
echo "DAMSR11 transport exit=$archive_status; archive acceptance remains pending; preserve disk"
# No done.json, no disk deletion, no cleanup acceptance; Root owns lifecycle.
shutdown -h now
exit "$archive_status"
