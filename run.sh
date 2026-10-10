#!/bin/bash
# One shared entry; public default is bounded local validation, never paid GCP.
set -euo pipefail
task_root=$(cd "$(dirname "$0")" && pwd)
cd "$task_root"
case "$(uname -s)" in Darwin|Linux) ;; *) echo 'Use a POSIX Linux/macOS terminal or WSL2; native Windows is not verified.' >&2; exit 2;; esac
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  cat <<'HELP'
Usage: ./run.sh [--spec validation|historical-full-study|full-study|governance-scale|scale-confirmation|longitudinal-adoption-5y|longitudinal-adoption-10y|small-enterprise-5y] [--scale PEOPLE]
                [--output DIRECTORY] [--runtime-limits JSON] [--prepare-only]
Defaults: bounded local validation, 120 people, runs/validation-n120.
Full research runs use the same versioned pipeline and automatic resource scheduler.
small-enterprise-5y requires explicit --scale 30|120|300, --output and
--runtime-limits; --prepare-only freezes its inventory without executing cases.
It uses the retained five-year paired-study helper, never a paid cloud default.
Advanced, explicitly authorized coordinator: set DAMS_CLOUD_PRIVATE_CONFIG to a
private mode-0600 file. Its explicit execution mode selects legacy GCS or
compute-only IAP phases; no public paid default or implicit storage fallback.
HELP
  exit 0
fi
task_spec=validation
task_scale=120
task_scale_explicit=false
task_output=
task_limits=
task_prepare=false
require_option_value() {
  if (($# < 2)) || [[ -z "$2" || "$2" == -* ]]; then
    echo "${1}: missing argument value; paths beginning with '-' require a './' prefix." >&2
    exit 2
  fi
}
while (($#)); do
  case "$1" in
    --spec) require_option_value "$@"; task_spec=$2; shift 2;;
    --scale) require_option_value "$@"; task_scale=$2; task_scale_explicit=true; shift 2;;
    --output) require_option_value "$@"; task_output=$2; shift 2;;
    --runtime-limits) require_option_value "$@"; task_limits=$2; shift 2;;
    --prepare-only) task_prepare=true; shift;;
    *) echo "Unknown argument: $1" >&2; exit 2;;
  esac
done
case "$task_spec" in validation|historical-full-study|full-study|governance-scale|scale-confirmation|longitudinal-adoption-5y|longitudinal-adoption-10y|small-enterprise-5y) ;; *) echo 'Unknown research spec.' >&2; exit 2;; esac
case "$task_scale" in ''|*[!0-9]*) echo 'Scale must be an integer population.' >&2; exit 2;; esac
if ((task_scale<2)); then echo 'Scale must be at least two people.' >&2; exit 2; fi
if [[ "$task_spec" == small-enterprise-5y ]]; then
  [[ "$task_scale_explicit" == true ]] || { echo 'Small-enterprise study requires explicit --scale 30, 120 or 300.' >&2; exit 2; }
  case "$task_scale" in 30|120|300) ;; *) echo 'Small-enterprise population must be 30, 120 or 300.' >&2; exit 2;; esac
  [[ -n "$task_output" ]] || { echo 'Small-enterprise study requires --output.' >&2; exit 2; }
  [[ -n "$task_limits" ]] || { echo 'Small-enterprise study requires --runtime-limits.' >&2; exit 2; }
  [[ -z "${DAMS_CLOUD_PRIVATE_CONFIG:-}" ]] || { echo 'Small-enterprise selector is local; private cloud execution requires a separately admitted coordinator.' >&2; exit 2; }
fi
if [[ -z "$task_output" ]]; then task_output="runs/${task_spec}-n${task_scale}"; fi
is_pinned_uv_version() {
  [[ "$1" == 'uv 0.9.26' || "$1" == 'uv 0.9.26 '* ]]
}
if command -v uv >/dev/null 2>&1 && is_pinned_uv_version "$(uv --version)"; then
  task_uv=$(command -v uv)
else
  # Official release artifacts pinned by SHA256; no sudo or profile changes.
  task_uv="$task_root/runs/.tools/uv/uv"
  if [[ ! -x "$task_uv" ]]; then
    [[ "${DAMS_OFFLINE_DEPENDENCIES:-0}" != 1 ]] || { echo 'Offline image lacks the fixed uv binary.' >&2; exit 2; }
    command -v curl >/dev/null || { echo 'curl is required for first installation.' >&2; exit 2; }
    case "$(uname -s)/$(uname -m)" in
      Darwin/arm64) task_target=aarch64-apple-darwin; task_sha=fcf0a9ea6599c6ae28a4c854ac6da76f2c889354d7c36ce136ef071f7ab9721f;;
      Darwin/x86_64) task_target=x86_64-apple-darwin; task_sha=171eb8c518313e157c5b4cec7b4f743bc6bab1bd23e09b646679a02d096a047f;;
      Linux/aarch64) task_target=aarch64-unknown-linux-gnu; task_sha=f71040c59798f79c44c08a7a1c1af7de95a8d334ea924b47b67ad6b9632be270;;
      Linux/x86_64) task_target=x86_64-unknown-linux-gnu; task_sha=30ccbf0a66dc8727a02b0e245c583ee970bdafecf3a443c1686e1b30ec4939e8;;
      *) echo 'No verified uv binary for this OS/architecture.' >&2; exit 2;;
    esac
    task_tmp=$(mktemp -d)
    trap 'rm -rf "$task_tmp"' EXIT
    curl --fail --location --proto '=https' --tlsv1.2 --retry 2 --max-time 180 \
      "https://github.com/astral-sh/uv/releases/download/0.9.26/uv-${task_target}.tar.gz" -o "$task_tmp/uv.tar.gz"
    if command -v sha256sum >/dev/null 2>&1; then
      task_actual=$(sha256sum "$task_tmp/uv.tar.gz" | cut -d' ' -f1)
    else
      task_actual=$(shasum -a 256 "$task_tmp/uv.tar.gz" | cut -d' ' -f1)
    fi
    [[ "$task_actual" == "$task_sha" ]] || { echo 'uv release checksum mismatch.' >&2; exit 2; }
    tar -xzf "$task_tmp/uv.tar.gz" -C "$task_tmp"
    mkdir -p "$(dirname "$task_uv")"
    cp "$task_tmp/uv-${task_target}/uv" "$task_uv"
    chmod 755 "$task_uv"
  fi
fi
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
task_uv_version=$("$task_uv" --version)
is_pinned_uv_version "$task_uv_version" || { echo 'uv tool version differs from the verified bootstrap version.' >&2; exit 2; }
echo "Using $task_uv_version at $task_uv; managed Python 3.14.2"
echo "Preparing locked dependencies; spec=$task_spec population=$task_scale output=$task_output"
task_sync_args=(sync --locked --extra analysis --python 3.14.2)
if [[ "${DAMS_OFFLINE_DEPENDENCIES:-0}" == 1 ]]; then task_sync_args+=(--offline); fi
"$task_uv" "${task_sync_args[@]}"
if [[ "$task_spec" == small-enterprise-5y ]]; then
  task_args=(--population "$task_scale" --output "$task_output" --runtime-limits "$task_limits")
  if [[ "$task_prepare" == true ]]; then task_args+=(--prepare-only); fi
  exec "$task_uv" run --no-sync python -m research_tools.small_enterprise "${task_args[@]}"
fi
if [[ "$task_prepare" == true ]]; then
  "$task_uv" run --no-sync python - "$task_spec" "$task_scale" <<'PY'
import sys
from dams_sim.spec import resolve_spec
name, scale = sys.argv[1], int(sys.argv[2])
if name == 'historical-full-study':
    if scale != 120:
        raise ValueError('historical design requires scale 120')
    print('Prepared immutable historical design at population 120')
else:
    spec = resolve_spec(name, scale)
    print(f'Prepared scientific specification: {spec.name}, N={spec.n}, days={spec.days}, SHA256={spec.sha256}')
PY
  exec "$task_uv" run --no-sync python -m dams_sim doctor
fi
if [[ -n "${DAMS_CLOUD_PRIVATE_CONFIG:-}" ]]; then
  # Science arguments stay explicit; controller rejects conflicts with its frozen plan.
  exec "$task_uv" run --no-sync python research_tools/cloud_control.py execute \
    --private-config "$DAMS_CLOUD_PRIVATE_CONFIG" --state-dir "${DAMS_CLOUD_STATE_DIR:?private persistent state directory required}" \
    --spec "$task_spec" --scale "$task_scale"
fi
task_args=(--spec "$task_spec" --scale "$task_scale" --output "$task_output")
if [[ -n "$task_limits" ]]; then task_args+=(--runtime-limits "$task_limits"); fi
exec "$task_uv" run --no-sync python -m dams_sim pipeline "${task_args[@]}"
