"""Read-only exact comparison; canonical outputs are never used as run inputs.

Fixed DAMS thesis protocol: core3d67/studybafa and2388 persisted worlds.
No numeric tolerance. CSV column/row order is normalized only by an exact
multiset of (field,string) pairs. State and summary files must match bytes.
Runtime/provenance exclusions are explicit and all differences are retained.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

CORE = "3d67a62e6eb2c78b4ac28aec497c7b5e43390d476cb8196309da0a9b9f644b90"
STUDY = "bafa8f5e2d0bf318696e8f4b8dda36ee48a5bf586009fa490b5c6938d1b84838"
STAGES = ("pilot", "confirmation", "mechanisms", "stress", "sensitivity", "scenarios", "extended", "recovery")
EXPECTED_PERSISTED_WORLDS = {"pilot":20,"confirmation":960,"mechanisms":0,"stress":496,"sensitivity":312,"scenarios":360,"extended":240,"recovery":0}
ORIGIN_COMMIT = "a9f6d711ca23f2bc9762b7a6d2bba42db95be275"
REQUIRED_CSV_ROWS = {
    "pilot": {"pilot_summary.csv":20},
    "confirmation": {"world_summary.csv":960},
    "mechanisms": {"fixed_stream.csv":40,"metric_replication.csv":4,"split_gain.csv":50},
    "stress": {"stress_summary.csv":496},
    "sensitivity": {"elementary_effects.csv":48,"sensitivity_runs.csv":168,"structural_alternatives.csv":144},
    "scenarios": {"scenario_summary.csv":360},
    "extended": {"extended_summary.csv":240},
    "recovery": {"candidate_training_patterns.csv":216,"heldout_prediction_patterns.csv":24,"output_identification_null.csv":8,"synthetic_observation_patterns.csv":72,"training_distances.csv":162},
}
REQUIRED_JSON = {
    "pilot": {"protocol.json"},
    "confirmation": {"case_statuses.json","ensemble_manifest.json","protocol.json"},
    "mechanisms": {"manifest.json"},
    "stress": {"case_statuses.json","ensemble_manifest.json"},
    "sensitivity": {"case_statuses.json","ensemble_manifest.json"},
    "scenarios": {"case_statuses.json","ensemble_manifest.json","scenario_definitions.json"},
    "extended": {"case_statuses.json","ensemble_manifest.json","extended_protocol.json"},
    "recovery": {"manifest.json","recovery_results.json"},
}
RECOVERY_FIELDS = {
    **{name: {"world","behavior_rule","autonomy_response","review_capacity_per_member_day","work_per_member_day","regret_per_guild_day","unfinished_per_member","mean_trust"} for name in ("candidate_training_patterns.csv","synthetic_observation_patterns.csv","heldout_prediction_patterns.csv")},
    "training_distances.csv": {"case","candidate","distance","behavior_rule","autonomy_response","review_capacity_per_member_day"},
    "output_identification_null.csv": {"world","linear_work","sublinear_work","exact_output_equivalence"},
}
PIPELINE_STAGES = ("tests","smoke",*STAGES,"analysis")
PROVENANCE = {"created_utc", "git_commit", "git_dirty", "python", "child_interpreter", "run_id"}
RUNTIME = {"compiled_kernel_seconds", "cpu_seconds", "initialization_wall_seconds", "simulation_wall_seconds", "statistics_wall_seconds", "plotting_seconds", "total_wall_seconds", "total_pilot_wall_seconds", "peak_process_rss_mb", "output_bytes"}
REBOUND_DIGESTS = {"output_sha256", "protocol_sha256"}

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def canonical_sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def csv_data(path):
    with path.open(newline="") as f:
        r = csv.DictReader(f)
        header = r.fieldnames
        if not header or any(not k for k in header) or len(set(header)) != len(header):
            raise ValueError("CSV requires nonempty unique field names")
        fields = tuple(sorted(header))
        rows = Counter()
        for number, row in enumerate(r,2):
            if None in row or any(row[k] is None for k in fields):
                raise ValueError(f"CSV row{number} does not have exactly the header field count")
            rows[tuple((k, row[k]) for k in fields)] += 1
    return fields, rows

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", type=Path, required=True, help="Reference complete reproduction run directory")
    ap.add_argument("--fresh", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True, help="Comparison evidence directory; no model inputs are copied")
    ap.add_argument("--expected-origin-commit", default=ORIGIN_COMMIT, help="Commit that actually produced the fresh run; default the validated a9f release origin")
    ap.add_argument("--expected-checkout-commit", help="Current clean checkout; default expected origin. Core/study identity is checked; this does not certify every change as font-only")
    args = ap.parse_args()
    if not args.reference.is_dir() or not args.fresh.is_dir() or not (args.fresh / "pipeline_manifest.json").is_file():
        ap.error("Both run directories and the fresh pipeline manifest must exist")
    fresh_repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=args.fresh, text=True).strip())
    try:
        reference_repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=args.reference, text=True, stderr=subprocess.DEVNULL).strip())
    except subprocess.CalledProcessError:
        reference_repo = args.reference.resolve()  # A downloaded reference archive need not be a Git checkout.
    expected_checkout = args.expected_checkout_commit or args.expected_origin_commit
    replacements = sorted(((str(args.reference.resolve()), "<reference-run>"), (str(args.fresh.resolve()), "<fresh-run>"), (str(reference_repo), "<reference-repository>"), (str(fresh_repo), "<fresh-repository>")), key=lambda x: -len(x[0]))
    def sanitized(value):
        if isinstance(value, str):
            for actual, portable in replacements:
                value = value.replace(actual, portable)
            if value.startswith("/"):
                value = "<recorded-absolute-path>/" + Path(value).name
            return value
        if isinstance(value, dict):
            return {k: sanitized(v) for k, v in value.items()}
        if isinstance(value, (tuple, list)):
            return [sanitized(v) for v in value]
        return value
    args.output.mkdir(parents=True, exist_ok=True)
    diffs = (args.output / "scientific-differences.jsonl").open("w")
    meta = (args.output / "provenance-runtime-differences.jsonl").open("w")
    inventory = []
    errors = []
    ignored = Counter()
    stage_results = []
    verified_files = Counter()
    verified_output_sizes = Counter()
    manifest_interpreters = {"canonical": Counter(), "fresh": Counter()}
    world_total = 0
    summary_same = state_same = trace_same = 0

    def error(where, kind, **kwargs):
        row = {"where": where, "kind": kind, **kwargs}
        errors.append(row)
        diffs.write(json.dumps(sanitized(row), sort_keys=True) + "\n")

    def deep_diff(a, b, where):
        if type(a) is not type(b):
            error(where, "type", canonical=a, fresh=b)
        elif isinstance(a, dict):
            for k in sorted(set(a) | set(b)):
                if k not in a or k not in b:
                    error(where + "/" + k, "missing_field", canonical=a.get(k), fresh=b.get(k))
                else:
                    deep_diff(a[k], b[k], where + "/" + k)
        elif isinstance(a, list):
            if len(a) != len(b):
                error(where, "list_length", canonical=len(a), fresh=len(b))
            for i, (x, y) in enumerate(zip(a, b)):
                deep_diff(x, y, where + "/" + str(i))
        elif a != b:
            values = {"canonical": a, "fresh": b}
            if isinstance(a, float) and isinstance(b, float):
                values.update(canonical_hex=a.hex(), fresh_hex=b.hex(), delta=b-a)
            error(where, "value", **values)

    def verify(path, manifest, label):
        hashes = manifest.get("output_sha256", {})
        for filename, expected in hashes.items():
            p = path / filename
            if not p.resolve().is_relative_to(path.resolve()) or not p.is_file():
                error(label + "/" + filename, "missing_or_outside_recorded_output")
            elif sha(p) != expected:
                error(label + "/" + filename, "integrity_mismatch", expected=expected, observed=sha(p))
            else:
                verified_files[label.split(":", 1)[0]] += 1

    def verify_world_size(path, manifest, side):
        # cli.run_world counts the five outputs and the *running* manifest,
        # before final timing/hash/driver metadata is appended. Provenance
        # string length therefore changes this measured I/O count. Recompute
        # it exactly rather than overlooking an unexplained numeric difference.
        keys = ("source_sha256", "git_commit", "git_dirty", "python", "platform", "machine", "dependencies", "container_image", "random_stream", "created_utc", "run_id", "status", "config_sha256", "config")
        running = {k: manifest[k] for k in keys}
        running["status"] = "running"
        size = len(json.dumps(running, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()) + 1
        size += sum(p.stat().st_size for p in path.iterdir() if p.is_file() and p.name != "manifest.json")
        if size != manifest["output_bytes"]:
            error(str(path), "output_bytes_not_exactly_recomputable", recorded=manifest["output_bytes"], recomputed=size)
        else:
            verified_output_sizes[side] += 1

    def metadata_compare(a, b, where):
        if not isinstance(a, dict) or not isinstance(b, dict):
            deep_diff(a, b, where)
            return
        for k in sorted(set(a) | set(b)):
            if a.get(k) == b.get(k) and k in a and k in b:
                continue
            if k in PROVENANCE | RUNTIME | REBOUND_DIGESTS:
                ignored[k] += 1
                meta.write(json.dumps(sanitized({"where": where + "/" + k, "classification": "provenance" if k in PROVENANCE else "runtime" if k in RUNTIME else "locally_verified_output_digest", "canonical": a.get(k), "fresh": b.get(k)}), sort_keys=True) + "\n")
            elif k not in a or k not in b:
                error(where + "/" + k, "metadata_missing_field", canonical=a.get(k), fresh=b.get(k))
            else:
                deep_diff(a[k], b[k], where + "/" + k)

    def index_worlds(root, stage, side):
        index = {}
        excluded = []
        for p in sorted((root / stage).rglob("manifest.json")):
            m = json.loads(p.read_text())
            if "config" not in m:
                continue
            if m.get("status") != "complete" or m.get("source_sha256") != CORE:
                excluded.append({"path": str(p.relative_to(root)), "status": m.get("status"), "source_sha256": m.get("source_sha256")})
                continue
            if side == "fresh" and (m.get("git_commit") != args.expected_origin_commit or m.get("git_dirty") is not False):
                error(str(p), "world_origin_binding_mismatch", expected=args.expected_origin_commit, actual=m.get("git_commit"),git_dirty=m.get("git_dirty"))
            key = m.get("config_sha256")
            if key != canonical_sha(m["config"]):
                error(str(p), "config_digest_mismatch")
            if m.get("research_driver_sha256") != STUDY:
                error(str(p), "study_driver_mismatch", observed=m.get("research_driver_sha256"))
            if key in index:
                error(stage + "/" + str(key), "duplicate_complete_config", side=side)
            index[key] = (p.parent, m)
            manifest_interpreters[side][m.get("python")] += 1
        return index, excluded

    for stage in STAGES:
        before_errors = len(errors)
        ca, ca_excluded = index_worlds(args.reference, stage, "canonical")
        fr, fr_excluded = index_worlds(args.fresh, stage, "fresh")
        for side, index in (("canonical", ca), ("fresh", fr)):
            if len(index) != EXPECTED_PERSISTED_WORLDS[stage]:
                error(stage, "fixed_protocol_world_count", side=side, expected=EXPECTED_PERSISTED_WORLDS[stage], actual=len(index))
        missing, extra = sorted(set(ca)-set(fr)), sorted(set(fr)-set(ca))
        if missing or extra:
            error(stage, "world_config_inventory", missing_fresh=missing, extra_fresh=extra)
        stage_row = {"stage": stage, "canonical_complete_worlds": len(ca), "fresh_complete_worlds": len(fr), "canonical_excluded_attempts": ca_excluded, "fresh_excluded_attempts": fr_excluded, "csv_files": [], "top_level_json_files": [], "state_byte_matches": 0, "summary_byte_matches": 0, "timeseries_byte_matches": 0}
        for key in sorted(set(ca) & set(fr)):
            cp, cm = ca[key]
            fp, fm = fr[key]
            label = stage + "/" + key
            verify(cp, cm, "canonical:" + label)
            verify(fp, fm, "fresh:" + label)
            verify_world_size(cp, cm, "canonical")
            verify_world_size(fp, fm, "fresh")
            metadata_compare(cm, fm, label + "/manifest.json")
            row = {"stage": stage, "config_sha256": key, "canonical_case": str(cp.relative_to(args.reference)), "fresh_case": str(fp.relative_to(args.fresh)), "canonical_output_bytes":cm['output_bytes'], "fresh_output_bytes":fm['output_bytes'], "output_byte_delta":fm['output_bytes']-cm['output_bytes']}
            for name in ("summary.json", "final_state.json", "timeseries.csv"):
                if name not in cm.get("output_sha256", {}) or name not in fm.get("output_sha256", {}):
                    error(label + "/" + name, "required_output_integrity_record_missing")
                    continue
                a, b = sha(cp / name), sha(fp / name)
                same = a == b
                row[name + ":canonical_sha256"] = a
                row[name + ":fresh_sha256"] = b
                row[name + ":byte_identical"] = same
                if same:
                    if name == "summary.json": summary_same += 1; stage_row["summary_byte_matches"] += 1
                    elif name == "final_state.json": state_same += 1; stage_row["state_byte_matches"] += 1
                    else: trace_same += 1; stage_row["timeseries_byte_matches"] += 1
                else:
                    error(label + "/" + name, "byte_difference", canonical_sha256=a, fresh_sha256=b)
                    if name.endswith(".json"):
                        deep_diff(json.loads((cp/name).read_text()), json.loads((fp/name).read_text()), label + "/" + name)
            inventory.append(row)
            world_total += 1
        csv_names = set(p.name for p in (args.reference/stage).glob("*.csv")) | set(p.name for p in (args.fresh/stage).glob("*.csv"))
        for name in sorted(set(REQUIRED_CSV_ROWS[stage])-csv_names):
            error(stage+"/"+name,"required_csv_missing_from_both_runs")
        for name in sorted(csv_names):
            cp, fp = args.reference/stage/name, args.fresh/stage/name
            if not cp.exists() or not fp.exists():
                error(stage + "/" + name, "csv_inventory")
                continue
            try:
                fields_c, rows_c = csv_data(cp)
                fields_f, rows_f = csv_data(fp)
            except (ValueError,csv.Error) as exc:
                error(stage+"/"+name,"malformed_csv",description=str(exc))
                continue
            if stage == "recovery" and name in RECOVERY_FIELDS:
                for side,fields in (("canonical",fields_c),("fresh",fields_f)):
                    missing=RECOVERY_FIELDS[name]-set(fields)
                    if missing:error(stage+"/"+name,"required_recovery_pattern_fields",side=side,missing=sorted(missing))
            if name in REQUIRED_CSV_ROWS[stage]:
                for side,rows in (("canonical",rows_c),("fresh",rows_f)):
                    if sum(rows.values()) != REQUIRED_CSV_ROWS[stage][name]:
                        error(stage+"/"+name,"fixed_protocol_csv_row_count",side=side,expected=REQUIRED_CSV_ROWS[stage][name],actual=sum(rows.values()))
            same = fields_c == fields_f and rows_c == rows_f
            detail = {"file": name, "canonical_rows": sum(rows_c.values()), "fresh_rows": sum(rows_f.values()), "canonical_sha256": sha(cp), "fresh_sha256": sha(fp), "byte_identical": sha(cp)==sha(fp), "exact_unordered_rows_equal": same, "numeric_tolerance": 0}
            stage_row["csv_files"].append(detail)
            if fields_c != fields_f:
                error(stage + "/" + name, "csv_fields", canonical=fields_c, fresh=fields_f)
            if rows_c != rows_f:
                for direction, counter in (("missing_fresh", rows_c-rows_f), ("extra_fresh", rows_f-rows_c)):
                    for values, count in counter.items():
                        error(stage + "/" + name, "csv_exact_row", direction=direction, count=count, row=dict(values))
        json_names = set(p.name for p in (args.reference/stage).glob("*.json")) | set(p.name for p in (args.fresh/stage).glob("*.json"))
        for name in sorted(REQUIRED_JSON[stage]-json_names):
            error(stage+"/"+name,"required_json_missing_from_both_runs")
        for name in sorted(json_names):
            cp, fp = args.reference/stage/name, args.fresh/stage/name
            if not cp.exists() or not fp.exists():
                error(stage + "/" + name, "json_inventory")
                continue
            a, b = json.loads(cp.read_text()), json.loads(fp.read_text())
            if stage == "recovery" and name == "recovery_results.json":
                for side,value in (("canonical",a),("fresh",b)):
                    if not isinstance(value,list) or len(value) != 6:
                        error(stage+"/"+name,"fixed_protocol_recovery_condition_count",side=side,expected=6)
            if stage == "confirmation" and name == "ensemble_manifest.json":
                for side,value,root in (("canonical",a,args.reference),("fresh",b,args.fresh)):
                    protocol=root/'pilot/protocol.json'
                    if not protocol.is_file() or value.get("protocol_sha256") != sha(protocol):
                        error(stage+"/"+name,"locally_rebound_protocol_digest_mismatch",side=side)
            if name in ("manifest.json","ensemble_manifest.json"):
                for side,value in (("canonical",a),("fresh",b)):
                    if value.get("status") != "complete" or value.get("source_sha256") != CORE:
                        error(stage+"/"+name,"stage_completion_or_core_binding_mismatch",side=side)
                    if stage in ("extended","recovery"):
                        expected_driver=sha(fresh_repo/'research_tools'/f'{stage}.py')
                        if value.get("driver_sha256") != expected_driver:
                            error(stage+"/"+name,"stage_driver_binding_mismatch",side=side,expected=expected_driver,actual=value.get("driver_sha256"))
            if isinstance(a, dict) and "output_sha256" in a: verify(cp.parent, a, "canonical:"+stage+"/"+name)
            if isinstance(b, dict) and "output_sha256" in b: verify(fp.parent, b, "fresh:"+stage+"/"+name)
            metadata_compare(a, b, stage + "/" + name)
            stage_row["top_level_json_files"].append({"file": name, "canonical_sha256": sha(cp), "fresh_sha256": sha(fp), "byte_identical": sha(cp)==sha(fp)})
        stage_row["scientific_or_integrity_difference_count"] = len(errors)-before_errors
        stage_results.append(stage_row)
        print(json.dumps({"stage":stage,"worlds":len(fr),"state_byte_matches":stage_row['state_byte_matches'],"differences":stage_row['scientific_or_integrity_difference_count']}), flush=True)

    fields = list(inventory[0]) if inventory else ["stage", "config_sha256"]
    with (args.output / "case-inventory.csv").open("w", newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(inventory)
    git = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=fresh_repo, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=fresh_repo, text=True).strip())
    pipeline=json.loads((args.fresh/"pipeline_manifest.json").read_text())
    if pipeline['status']!='complete' or pipeline['exit_code']!=0 or any(x['status']!='passed' for x in pipeline['stages']):error('pipeline','not_complete')
    if tuple(x['stage'] for x in pipeline['stages']) != PIPELINE_STAGES:error('pipeline','fixed_protocol_stage_inventory')
    for record in pipeline['stages']:
        log=args.fresh/(record['stage']+'.log')
        if not log.is_file() or sha(log)!=record.get('log_sha256'):error('pipeline/'+record['stage'],'stage_log_integrity_mismatch')
    for name, expected in (("source_sha256",CORE),("study_driver_sha256",STUDY),("git_commit",args.expected_origin_commit),("git_dirty",False)):
        if pipeline.get(name) != expected:error('pipeline/'+name,'binding_mismatch',expected=expected,actual=pipeline.get(name))
    if git != expected_checkout or dirty:error('checkout','expected_checkout_or_cleanliness_mismatch',expected=expected_checkout,actual=git,git_dirty=dirty)
    current_core=hashlib.sha256(b''.join(p.name.encode()+b'\0'+p.read_bytes() for p in sorted((fresh_repo/'dams_sim').glob('*.py')))).hexdigest()
    current_study=sha(fresh_repo/'research_tools/study.py')
    if current_core != CORE or current_study != STUDY:error('checkout','current_scientific_source_mismatch',core_sha256=current_core,study_sha256=current_study)
    diffs.close();meta.close()
    report={"status":"passed" if not errors else "failed", "completed_utc":datetime.now(timezone.utc).isoformat(), "source_sha256":CORE, "study_driver_sha256":STUDY, "fresh_origin_git_commit":pipeline["git_commit"], "fresh_current_checkout_git_commit":git, "expected_origin_git_commit":args.expected_origin_commit, "expected_checkout_git_commit":expected_checkout, "current_checkout_core_sha256":current_core, "current_checkout_study_sha256":current_study, "fresh_git_dirty":dirty, "canonical_root":str(args.reference), "fresh_root":str(args.fresh), "compared_complete_persisted_worlds":world_total, "summary_byte_matches":summary_same, "final_state_byte_matches":state_same, "timeseries_byte_matches":trace_same, "locally_integrity_verified_recorded_files":dict(verified_files), "locally_recomputed_output_size_worlds":dict(verified_output_sizes), "output_size_explanation":"output_bytes counts the five outputs plus the running manifest, whose provenance string lengths can differ. Every recorded size is independently recomputed exactly; scientific files must still match bytes.", "observed_output_byte_delta_counts":dict(Counter(str(row["output_byte_delta"]) for row in inventory)), "scientific_or_integrity_difference_count":len(errors), "no_numeric_tolerance":True,"csv_order_only_normalization":True,"ignored_metadata_fields_and_difference_counts":dict(ignored), "interpreters_from_actual_world_manifests":{s:dict(c) for s,c in manifest_interpreters.items()}, "recovery_scope":{"model_executions":328,"candidate_training_pattern_rows":216,"synthetic_observation_pattern_rows":72,"heldout_prediction_pattern_rows":24,"output_null_world_rows":8,"output_null_model_executions":16,"selection_conditions":6,"persisted_full_states":0,"statement":"Recovery persists its four exact scientific moments/patterns and fitted selections/distances, not complete final states. No recovery-state identity claim is made."}, "pipeline_stage_results":pipeline['stages'], "stages":stage_results,"comparator_sha256":sha(Path(__file__))}
    (args.output/"comparison.json").write_text(json.dumps(sanitized(report),indent=2,sort_keys=True)+"\n")
    print(json.dumps({k:report[k] for k in ('status','compared_complete_persisted_worlds','summary_byte_matches','final_state_byte_matches','timeseries_byte_matches','scientific_or_integrity_difference_count','fresh_origin_git_commit','fresh_git_dirty')},indent=2))
    raise SystemExit(0 if not errors and not dirty else 1)

if __name__ == '__main__': main()
