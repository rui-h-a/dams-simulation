#!/bin/bash
# Fetch an explicit trusted public commit, then use the SAME run.sh entry.
set -euo pipefail
task_commit=${DAMS_RELEASE_COMMIT:-}
if [[ "${1:-}" == --commit ]]; then task_commit=${2:?full immutable commit required}; shift 2; fi
if [[ ! "$task_commit" =~ ^[a-f0-9]{40}$ ]]; then
  echo 'Bootstrap needs a full published release commit (--commit SHA); use the pinned one-line release command.' >&2
  exit 2
fi
command -v git >/dev/null || { echo 'git is required to verify a fixed source commit.' >&2; exit 2; }
task_base=${XDG_DATA_HOME:-$HOME/.local/share}/dams-simulation
task_dir="$task_base/$task_commit"
mkdir -p "$task_base"
if [[ ! -d "$task_dir" ]]; then
  task_tmp=$(mktemp -d "$task_base/.fetch-XXXXXX")
  trap 'rm -rf "$task_tmp"' EXIT
  git -C "$task_tmp" init -q
  git -C "$task_tmp" remote add origin https://github.com/rui-h-a/dams-simulation.git
  git -C "$task_tmp" fetch --depth 1 origin "$task_commit"
  git -C "$task_tmp" checkout --detach FETCH_HEAD
  [[ "$(git -C "$task_tmp" rev-parse HEAD)" == "$task_commit" ]] || { echo 'Source commit verification failed.' >&2; exit 2; }
  mv "$task_tmp" "$task_dir"
  trap - EXIT
fi
[[ "$(git -C "$task_dir" rev-parse HEAD)" == "$task_commit" ]] || { echo 'Existing install has wrong commit.' >&2; exit 2; }
[[ -z "$(git -C "$task_dir" status --porcelain --untracked-files=all)" ]] || { echo 'Existing install has modified or untracked source; retained, refusing reuse.' >&2; exit 2; }
exec /bin/bash "$task_dir/run.sh" "$@"
