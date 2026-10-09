"""Private GCP orchestration: durable reservations precede every paid action.

No implicit authorization, account discovery, on-demand fallback, or new budget
on resume. The coordinator supplies a private JSON configuration. `plan` and
`package` are local; only `execute` invokes mutating cloud commands.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
HEX = re.compile(r"^[0-9a-f]{40}$")
NAME = re.compile(r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$")
ALLOW = ("dams_sim", "research_tools", "tests", "cloud", "docs", "run.sh",
         "bootstrap.sh", "pyproject.toml", "uv.lock", "LICENSE", "CITATION.cff", "README.md")


class GuardError(RuntimeError):
    pass


def stage_capabilities(c, s):
    """Resolve a frozen single-VM profile; never infer a purchase fallback.

    Missing mode retains the historical C4D Spot contract. M3/C4N Standard is an
    explicit opt-in with catalog-bound guest expectations and quota dimensions.
    The catalog is a planning input, not evidence of capacity or guest hardware.
    """
    mode = s.get("purchase_mode", "SPOT")
    machine = s["machine_type"]
    if mode == "SPOT" and machine in ("c4d-highmem-4", "c4d-highmem-96", "c4d-highmem-192", "c4d-highmem-384"):
        allowed_disks = ("hyperdisk-balanced",)
        quota = {"metric": "PREEMPTIBLE_CPUS", "info_id": "PREEMPTIBLE-CPUS-per-project-region",
                 "dimensions": {"region": c["region"]}}
    elif mode == "STANDARD" and machine == "c4d-highmem-4":
        allowed_disks = ("hyperdisk-balanced",)
        quota = {"metric": "CPUS_PER_VM_FAMILY", "info_id": "CPUS-PER-VM-FAMILY-per-project-region",
                 "dimensions": {"region": c["region"], "vm_family": "C4D"}}
        if "disk_type" not in s or s.get("quota") != quota:
            raise GuardError("Standard C4D4 requires explicit Hyperdisk and its exact regional family quota")
        catalog = c["price_snapshot"].get("machines", {}).get(machine, {})
        if (type(catalog.get("vcpus")) is not int or catalog["vcpus"] != 4
                or money(catalog.get("memory_gib", 0)) != 31 or catalog.get("architecture") != "X86_64"
                or not catalog.get("zone") or c["zones"] != [catalog["zone"]]
                or not catalog["zone"].startswith(c["region"] + "-")):
            raise GuardError("Standard C4D4 catalog CPU/RAM/architecture/zone differs")
        expected = s.get("expected_guest", {})
        if (not isinstance(expected, dict) or set(expected) != {"architecture", "vcpus", "memory_gib_min", "memory_gib_max"}
                or expected["architecture"] != "x86_64" or type(expected["vcpus"]) is not int or expected["vcpus"] != 4
                or not 0 < money(expected["memory_gib_min"]) < money(expected["memory_gib_max"])
                or money(expected["memory_gib_max"]) != 31):
            raise GuardError("Standard C4D4 requires exact CPU/architecture and usable/catalog RAM bounds")
    elif mode == "STANDARD" and machine in ("m3-ultramem-32", "m3-ultramem-64", "m3-ultramem-128"):
        allowed_disks = ("pd-balanced", "pd-ssd", "hyperdisk-balanced")
        quota = {"metric": "M3_CPUS", "info_id": "M3-CPUS-per-project-region",
                 "dimensions": {"region": c["region"]}}
        if "disk_type" not in s or s.get("quota") != quota:
            raise GuardError("Standard M3 requires an explicit compatible disk and its exact regional quota profile")
        catalog = c["price_snapshot"].get("machines", {}).get(machine, {})
        catalog_memory = {"m3-ultramem-32": 976, "m3-ultramem-64": 1952, "m3-ultramem-128": 3904}[machine]
        if money(catalog.get("memory_gib", 0)) != catalog_memory:
            raise GuardError("Standard M3 ultramem catalog memory differs from the predefined shape")
        expected = s.get("expected_guest", {})
        if (set(expected) != {"architecture", "vcpus", "memory_gib_min", "memory_gib_max"}
                or expected.get("architecture") != "x86_64"
                or type(expected.get("vcpus")) is not int
                or expected["vcpus"] != catalog.get("vcpus")
                or expected["vcpus"] != int(machine.rsplit("-", 1)[-1])):
            raise GuardError("Standard M3 requires catalog-bound, explicit guest CPU/architecture expectations")
        minimum, maximum = money(expected["memory_gib_min"]), money(expected["memory_gib_max"])
        if minimum <= 0 or minimum >= maximum or maximum != money(catalog.get("memory_gib", 0)):
            raise GuardError("guest RAM range must specify a positive usable minimum, an OS margin and the exact catalog maximum")
    elif mode == "STANDARD" and machine in ("c4n-highcpu-192", "c4n-highmem-192"):
        allowed_disks = ("hyperdisk-balanced",)
        quota = {"metric": "CPUS_PER_VM_FAMILY", "info_id": "CPUS-PER-VM-FAMILY-per-project-region",
                 "dimensions": {"region": c["region"], "vm_family": "C4N"}}
        if "disk_type" not in s or s.get("quota") != quota:
            raise GuardError("Standard C4N requires its explicit Hyperdisk and exact regional family quota profile")
        catalog = c["price_snapshot"].get("machines", {}).get(machine, {})
        memory = {"c4n-highcpu-192": 384, "c4n-highmem-192": 1488}[machine]
        if (type(catalog.get("vcpus")) is not int or catalog["vcpus"] != 192
                or money(catalog.get("memory_gib", 0)) != memory or catalog.get("architecture") != "X86_64"
                or not catalog.get("zone") or c["zones"] != [catalog["zone"]]
                or not catalog["zone"].startswith(c["region"] + "-")):
            raise GuardError("Standard C4N catalog CPU/RAM/architecture/zone differs from the frozen described shape")
        expected = s.get("expected_guest", {})
        if (not isinstance(expected, dict)
                or set(expected) != {"architecture", "vcpus", "memory_gib_min", "memory_gib_max"}
                or expected["architecture"] != "x86_64" or type(expected["vcpus"]) is not int
                or expected["vcpus"] != 192):
            raise GuardError("Standard C4N requires explicit catalog-bound guest CPU/architecture expectations")
        minimum, maximum = money(expected["memory_gib_min"]), money(expected["memory_gib_max"])
        if not 0 < minimum < maximum or maximum != money(memory):
            raise GuardError("C4N usable RAM requires an explicit positive minimum, OS margin and exact catalog maximum")
    else:
        raise GuardError("unsupported machine/purchase profile; no implicit Standard or Spot fallback")
    if s.get("quota", quota) != quota:
        raise GuardError("frozen quota pool/dimensions differ from the selected purchase profile")
    if type(s.get("node_count", 1)) is not int or s.get("node_count", 1) != 1 or s.get("nodes"):
        raise GuardError("multi-node execution is not implemented or verified by this single-VM adapter")
    disk = s.get("disk_type", "hyperdisk-balanced")
    if disk not in allowed_disks:
        raise GuardError("boot disk is incompatible with the selected machine profile")
    if type(s.get("disk_gib", 50)) is not int or not 20 <= s.get("disk_gib", 50) <= 65536:
        raise GuardError("boot disk size must be an explicit supported integer GiB capacity")
    if disk == "hyperdisk-balanced":
        if s.get("disk_iops", 3000) != 3000 or s.get("disk_throughput_mibps", 140) != 140:
            raise GuardError("this controller prices only baseline Hyperdisk performance")
    elif "disk_iops" in s or "disk_throughput_mibps" in s:
        raise GuardError("Persistent Disk must not inherit Hyperdisk performance provisioning")
    return {"purchase_mode": mode, "disk_type": disk, "disk_interface": "NVME", "quota": quota}



def stage_termination_action(c, s, *, compute_only):
    """Only an explicit bounded compute-only preservation phase may STOP.

    STOP retains the sole boot disk after the CPU deadline. It does not delete
    storage or authorize restart; root must recover/ACK and delete by global D.
    Historical GCS and missing action keep provider DELETE unchanged.
    """
    action = s.get("termination_action", "DELETE")
    if action not in ("DELETE", "STOP"):
        raise GuardError("unsupported frozen instance termination action")
    if action == "STOP":
        limits = s.get("runtime_limits", {})
        fields = ("phase_max_output_bytes", "phase_spool_bytes",
                  "phase_checkpoint_headroom_bytes", "phase_metadata_reserve_bytes")
        if (not compute_only or c.get("execution_mode") != "compute-only-iap"
                or s.get("purchase_mode") != "STANDARD" or s.get("machine_type") != "c4d-highmem-4"
                or not isinstance(limits, dict)
                or any(type(limits.get(k)) is not int or limits[k] <= 0 for k in fields)):
            raise GuardError("STOP requires the explicit bounded compute-only Standard C4D4 preservation phase")
    return action


def stage_vm_rate(c, s):
    mode = stage_capabilities(c, s)["purchase_mode"]
    rates = c["price_snapshot"].get("spot_vm_usd_per_hour" if mode == "SPOT" else "standard_vm_usd_per_hour", {})
    if s["machine_type"] not in rates or money(rates[s["machine_type"]]) <= 0:
        raise GuardError("machine lacks a positive checked rate for its frozen purchase mode")
    return money(rates[s["machine_type"]])


def stage_disk_rate(c, s):
    caps = stage_capabilities(c, s)
    p = c["price_snapshot"]
    # Only the historical Spot profile can use its historical scalar rate.
    rate = (p.get("disk_gib_hour", {}).get(caps["disk_type"])
            if caps["purchase_mode"] == "STANDARD" or "disk_gib_hour" in p
            else p.get("hyperdisk_gib_hour"))
    if rate is None or money(rate) <= 0:
        raise GuardError("selected disk type lacks a positive checked capacity rate")
    return money(rate)


def stamp(value=None):
    return (value or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")


def utc(value):
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise GuardError("timestamps require an explicit UTC offset")
    return d.astimezone(timezone.utc)


def money(value):
    try:
        d = Decimal(str(value))
    except InvalidOperation as e:
        raise GuardError("invalid money/rate") from e
    if not d.is_finite() or d < 0:
        raise GuardError("money/rates must be finite and nonnegative")
    return d


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=".atomic-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def locked(path):
    # Both control and its ledger are POSIX-only. WSL2 provides this contract.
    import fcntl
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+") as f:
        os.chmod(path, 0o600)
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def checked_config(c, now=None):
    now = now or datetime.now(timezone.utc)
    if c.get("execution_mode", "legacy-gcs") not in ("legacy-gcs", "compute-only-iap"):
        raise GuardError("unknown execution mode; no implicit GCS fallback")
    required = {"version", "authorization_id", "project", "region", "zones", "bucket",
                "service_account", "subnet", "image", "source_commit", "global_deadline_utc",
                "budget_cap_usd", "reserve_usd", "prior_spend_usd", "price_snapshot", "stages",
                "gcloud_configuration", "gcloud_account", "image_id", "network_mode"}
    if required - c.keys() or c.get("version") != 1:
        raise GuardError("private configuration is incomplete or has unsupported version")
    for key in ("project", "authorization_id"):
        if not NAME.fullmatch(c[key]):
            raise GuardError(f"invalid {key}")
    if not HEX.fullmatch(c["source_commit"]):
        raise GuardError("source_commit must be a complete immutable git SHA")
    if utc(c["global_deadline_utc"]) <= now:
        raise GuardError("absolute task deadline has expired")
    if not c["zones"] or any(not z.startswith(c["region"] + "-") for z in c["zones"]):
        raise GuardError("all candidate zones must be in the explicitly priced region")
    if not c["image"].startswith("https://") or "/global/images/" not in c["image"] or "/family/" in c["image"]:
        raise GuardError("use an immutable image selfLink, never an image family")
    if not c["service_account"].endswith(".iam.gserviceaccount.com"):
        raise GuardError("an explicitly attached, restricted service account is required")
    if c.get("dedicated_project") is not True:
        raise GuardError("this controller requires an explicitly dedicated research project")
    if not c.get("outbound_access_verified"):
        raise GuardError("verify API/package outbound access before creating a VM")
    if c["network_mode"] not in ("internal-offline", "iap-ephemeral-ip"):
        raise GuardError("choose an explicitly supported outbound/dependency path")
    if c["network_mode"] == "internal-offline" and not c.get("image_dependencies_preinstalled"):
        raise GuardError("internal-only mode requires a fixed image with the locked dependencies installed")
    if c["network_mode"] == "iap-ephemeral-ip" and not c.get("restricted_firewall_verified"):
        raise GuardError("ephemeral egress requires verified IAP-only management and bounded HTTPS egress")
    p = c["price_snapshot"]
    if p.get("region") != c["region"] or p.get("currency") != "USD":
        raise GuardError("rates must match region and USD ledger")
    age = (now - utc(p["checked_utc"])).total_seconds()
    if not 0 <= age <= 24 * 3600 or not p.get("sources"):
        raise GuardError("prices require primary-source evidence checked within 24 hours")
    if not c["stages"] or len({s["id"] for s in c["stages"]}) != len(c["stages"]):
        raise GuardError("stages require unique immutable IDs")
    if c.get("execution_topology", "single-vm") != "single-vm" or c.get("node_count", 1) != 1 or c.get("nodes"):
        raise GuardError("multi-node scheduling/network/global-save/cost plan is not implemented in this adapter")
    ids = [s["id"] for s in c["stages"]]
    selected = c.get("execution_stage_ids", ids)
    if not selected or len(set(selected)) != len(selected) or [x for x in ids if x in selected] != selected:
        raise GuardError("execution_stage_ids must be an ordered nonempty subset of the fully reserved plan")
    if not 1 <= c.get("max_create_attempts", 2) <= 3:
        raise GuardError("create attempts must be bounded to 1..3")
    for s in c["stages"]:
        if not NAME.fullmatch(s["id"]):
            raise GuardError("invalid stage ID")
        stage_capabilities(c, s)
        stage_termination_action(c, s, compute_only=c.get("execution_mode") == "compute-only-iap")
        stage_vm_rate(c, s)
        stage_disk_rate(c, s)
        if not isinstance(s["max_seconds"], int) or s["max_seconds"] < 180:
            raise GuardError("stage time must include startup, compute, upload and shutdown")
        if s["spec"] not in ("historical-full-study", "full-study", "validation", "governance-scale", "scale-confirmation", "longitudinal-adoption-5y", "longitudinal-adoption-10y") or not isinstance(s["scale"], int) or s["scale"] < 2:
            raise GuardError("invalid scientific specification/scale")
        if not s.get("runtime_limits") or s["runtime_limits"].get("version") != 1:
            raise GuardError("each stage requires explicit versioned scientific resource limits")
        stage_archive_options(s)
        for field in ("max_result_gib", "max_egress_gib", "storage_operations_usd_upper", "other_usd_upper"):
            money(s[field])
        duration=money(s['max_seconds'])/3600
        if money(s.get('disk_retention_hours',duration))<duration or money(s.get('storage_retention_hours',168))<duration:
            raise GuardError('disk/object retention reservation must cover at least the complete stage duration')
        if money(s['max_result_gib'])<=0 or money(s['max_egress_gib'])<=0:
            raise GuardError('complete results require positive storage/download reservations')
        storage_request_allowance(c,s)
        stage_stop_options(c, s)
    if money(c.get("cost_margin", "1.2")) < 1:
        raise GuardError("cost margin cannot discount reserved fees")
    cap, reserve, prior = map(money, (c["budget_cap_usd"], c["reserve_usd"], c["prior_spend_usd"]))
    if cap <= reserve + prior or prior + reserve + sum(stage_cost(c, s) for s in c["stages"]) > cap:
        raise GuardError("all stages and cleanup reserve must fit the same cumulative budget")
    return c


def stage_archive_options(s):
    """Freeze transport and raw restore capacity independently of compression."""
    codec = s.get("transport_codec", "raw-v1")
    if codec not in ("raw-v1", "deflate-chunks-v1"):
        raise GuardError("unsupported stage transport codec")
    fields = ("archive_max_raw_bytes", "archive_min_free_bytes")
    if codec == "raw-v1":
        if any(field in s for field in (*fields, 'archive_max_files')):
            raise GuardError("raw transport cannot silently ignore archive capacity fields")
        return {"transport_codec": codec}
    for field, minimum, maximum in (
        (fields[0], 1_000_000, 100_000_000_000_000),
        (fields[1], 0, 10_000_000_000_000),
    ):
        value = s.get(field)
        if type(value) is not int or not minimum <= value <= maximum:
            raise GuardError("compressed transport requires bounded integer " + field)
    # Atomic restore keeps the old tree until all reconstructed bytes verify.
    # Reserve both trees and free space; a sampled ratio cannot reduce this.
    capacity = money(s.get("disk_gib", 50)) * 1024**3
    if 2 * s[fields[0]] + s[fields[1]] > capacity:
        raise GuardError("disk cannot reserve old and staged raw archive trees")
    options = {"transport_codec": codec, **{field: s[field] for field in fields}}
    if 'archive_max_files' in s:
        files = s['archive_max_files']
        if type(files) is not int or not 1 <= files <= 8192:
            raise GuardError('archive file count must fit the trusted 8192-file bound')
        options['archive_max_files'] = files
    return options


def stage_stop_options(c, s):
    """Keep checkpoint grace and final persistence inside existing limits."""
    options = {}
    limits = s['runtime_limits']
    if 'stop_cutoff_utc' in limits:
        raise GuardError('cloud stop cutoff is derived from the reserved server deadline')
    if 'pipeline_stop_grace_seconds' in s:
        grace = s['pipeline_stop_grace_seconds']
        cooperative = limits.get('cooperative_stop_grace_seconds', 10)
        margin = s.get('shutdown_margin_seconds', 180)
        if (type(grace) is not int or type(cooperative) is not int
                or type(margin) is not int or cooperative < 1
                or grace < cooperative + 60 or not grace < margin
                or grace + margin >= s['max_seconds']):
            raise GuardError('pipeline grace must cover cooperative checkpoint/reap and leave the archive margin')
        options['pipeline_stop_grace_seconds'] = grace
    elif 'cooperative_stop_grace_seconds' in limits:
        raise GuardError('cloud cooperative grace requires an explicit covering pipeline grace')
    if 'final_storage_requests_reserved' in s:
        reserve = s['final_storage_requests_reserved']
        allowance = storage_request_allowance(c, s)
        if type(reserve) is not int or not 1 <= reserve <= allowance - 10:
            raise GuardError('final storage requests must leave startup/periodic requests inside the actor allowance')
        options['final_storage_requests_reserved'] = reserve
    if 'final_storage_bytes_reserved' in s:
        reserve = s['final_storage_bytes_reserved']
        total = int(money(s['max_result_gib']) * 1024**3)
        if type(reserve) is not int or not 1 <= reserve < total:
            raise GuardError('final storage bytes must leave periodic space inside the original storage cap')
        options['final_storage_bytes_reserved'] = reserve
    if 'preserve_interrupted_evidence' in s:
        enabled = s['preserve_interrupted_evidence']
        if type(enabled) is not bool or (enabled and not all(field in options for field in (
                'final_storage_requests_reserved', 'final_storage_bytes_reserved'))):
            raise GuardError('interrupted evidence requires an explicit boolean and final request/byte reserves')
        options['preserve_interrupted_evidence'] = enabled
    coordinator_archive_reservation(c, s)
    return options


def coordinator_archive_reservation(c, s):
    """Split one coordinator share; external archival gets no extra budget."""
    fields = ('archive_source_requests_reserved', 'archive_source_egress_gib_reserved')
    if not any(field in s for field in fields):
        return 0, 0
    if not all(field in s for field in fields):
        raise GuardError('external archive requires both request and transfer reservations')
    requests = s[fields[0]]
    if type(requests) is not int or not 1 <= requests <= storage_request_allowance(c, s) - 10:
        raise GuardError('archive requests must fit inside the existing coordinator allowance')
    transfer = int(money(s[fields[1]]) * 1024**3)
    total = int(money(s['max_egress_gib']) * 1024**3)
    if not 1 <= transfer < total:
        raise GuardError('archive transfer must leave a collector share inside the original egress cap')
    return requests, transfer


def coordinator_request_allowance(c, s):
    requests, _ = coordinator_archive_reservation(c, s)
    return storage_request_allowance(c, s) - requests


def coordinator_transfer_allowance(c, s):
    _, transfer = coordinator_archive_reservation(c, s)
    return int(money(s['max_egress_gib']) * 1024**3) - transfer


def stage_evidence_download_options(s):
    # Root collection can preserve forensic bytes. Guest auto-resume keeps the
    # reader's default refusal and never receives this opt-in.
    return {'allow_interrupted_evidence': True} if s.get('preserve_interrupted_evidence') is True else {}


def stage_science_deadlines(s, termination_utc):
    deadline = utc(termination_utc)
    margin = s.get('shutdown_margin_seconds', 180)
    grace = s.get('pipeline_stop_grace_seconds', 60)
    limits = {'deadline_utc': stamp(deadline - timedelta(seconds=margin + grace))}
    if 'pipeline_stop_grace_seconds' in s:
        limits['stop_cutoff_utc'] = stamp(deadline - timedelta(seconds=margin))
    return limits


def stage_cost(c, s):
    """Conservative reservation, including all configured fixed/noncompute costs.

    No free credits, discounts, tax exemption, or billing latency are credited.
    Retries have the SAME stage deadline/reservation; a new stage is never free.
    """
    p = c["price_snapshot"]
    hours = Decimal(s["max_seconds"]) / 3600
    storage_h = money(s.get("storage_retention_hours", 168))
    mode = stage_capabilities(c, s)["purchase_mode"]
    vm = stage_vm_rate(c, s) * hours
    # Boot/work disk remains reserved to global cleanup even if CPU stops early.
    disk = stage_disk_rate(c, s) * money(s.get("disk_gib", 50)) * money(s.get("disk_retention_hours", s["max_seconds"] / 3600))
    gcs = money(p["gcs_gib_hour"]) * (money(s["max_result_gib"])+money(s.get('source_package_gib_upper','0.01'))) * storage_h
    transfer = money(p["egress_gib"]) * money(s["max_egress_gib"])
    operations = money(s["storage_operations_usd_upper"])
    other = money(s["other_usd_upper"])  # includes IP/NAT/logging if introduced
    if c.get("network_mode") == "iap-ephemeral-ip":
        ip_rate = p.get("spot_external_ip_hour" if mode == "SPOT" else "standard_external_ip_hour")
        if ip_rate is None or money(ip_rate) <= 0 or other < hours * money(ip_rate):
            raise GuardError("mode-specific ephemeral IPv4 fee is missing from the noncompute reservation")
    return (vm + disk + gcs + transfer + operations + other) * money(c.get("cost_margin", "1.2"))


def storage_request_allowance(c,s):
    # Price every JSON-API HTTP attempt as the more expensive Class A unit.
    # Each bounded guest attempt and the persistent coordinator get one share.
    # A separate 64-request margin covers the two small source SDK uploads and
    # bucket/preflight metadata. SDK internals are not claimed fully metered.
    unit=money(c['price_snapshot']['gcs_class_a_per_1000'])/1000
    if unit<=0:raise GuardError('storage operation rate must be positive')
    total=int(money(s['storage_operations_usd_upper'])/unit)
    per_actor=(total-64)//(c.get('max_create_attempts',2)+1)
    if per_actor<10:raise GuardError('storage operation reservation cannot cover bounded actors and source staging')
    return per_actor


class Ledger:
    def __init__(self, path, c):
        self.path, self.c = Path(path), c

    def change(self, fn):
        with locked(self.path.with_suffix(".lock")):
            plan_identity=digest({"stages":self.c["stages"],"source":self.c["source_commit"],
                  "control":{k:self.c.get(k) for k in ('project','region','zones','bucket','service_account','subnet','image','image_id','network_mode','gcloud_configuration','gcloud_account','max_create_attempts','cost_margin')}})
            if self.path.exists():
                d = json.loads(self.path.read_text())
            else:
                d = {"version": 1, "authorization_id": self.c["authorization_id"],
                     "deadline_utc": self.c["global_deadline_utc"],
                     "cap_usd": str(money(self.c["budget_cap_usd"])),
                     "reserve_usd": str(money(self.c["reserve_usd"])),
                     "prior_spend_usd": str(money(self.c["prior_spend_usd"])),
                     "frozen_plan_sha256":plan_identity,
                     "entries": {}, "events": []}
            immutable = (d["authorization_id"] == self.c["authorization_id"] and
                         utc(d["deadline_utc"]) == utc(self.c["global_deadline_utc"]) and
                         money(d["cap_usd"]) == money(self.c["budget_cap_usd"]) and
                         money(d["reserve_usd"]) == money(self.c["reserve_usd"]) and
                         money(d["prior_spend_usd"]) == money(self.c["prior_spend_usd"]))
            immutable = immutable and d.get('frozen_plan_sha256')==plan_identity
            if not immutable:
                raise GuardError("existing authorization/budget/deadline cannot be reset on resume")
            result = fn(d)
            atomic(self.path, d)
            return result

    def reserve(self, s, now=None):
        now = now or datetime.now(timezone.utc)
        intent = digest({"source": self.c["source_commit"], "stage": s,
                         "cloud_resource_identity":{key:self.c[key] for key in ('project','region','bucket','service_account','subnet','image','image_id','network_mode','gcloud_configuration','gcloud_account')}})
        def update(d):
            entries = d["entries"]
            if s["id"] in entries:
                e = entries[s["id"]]
                if e["intent_sha256"] != intent:
                    raise GuardError("same stage ID cannot silently change its frozen intent")
                if stage_cost(self.c, s) > money(e["reserved_usd"]):
                    raise GuardError("newly checked prices exceed the original stage reservation")
                return e.copy()
            if now >= utc(d["deadline_utc"]):
                raise GuardError("global deadline expired")
            if any(e["state"] != "settled" for e in entries.values()):
                raise GuardError("one VM at a time: reconcile and clean the active stage first")
            committed = sum(money(e["charged_usd_upper"]) for e in entries.values())
            pending = sum(stage_cost(self.c, x) for x in self.c["stages"] if x["id"] not in entries)
            if money(d["prior_spend_usd"]) + committed + pending + money(d["reserve_usd"]) > money(d["cap_usd"]):
                raise GuardError("no affordable reservation including future stages and cleanup")
            expiry = min(now + timedelta(seconds=s["max_seconds"]), utc(d["deadline_utc"]))
            if (expiry - now).total_seconds() < s["max_seconds"]:
                raise GuardError("entire reserved stage cannot fit before global deadline")
            e = {"intent_sha256": intent, "state": "reserved", "reserved_usd": str(stage_cost(self.c, s)),
                 "charged_usd_upper": "0", "reserved_utc": stamp(now), "termination_utc": stamp(expiry),
                 "instance_name": "dams-" + digest([self.c["authorization_id"], s["id"]])[:24],
                 "attempts": 0}
            entries[s["id"]] = e
            d["events"].append({"utc": stamp(now), "stage": s["id"], "event": "reserved-before-cloud-action"})
            return e.copy()
        return self.change(update)

    def event(self, sid, **fields):
        def update(d):
            d["entries"][sid].update(fields)
            d["events"].append({"utc": stamp(), "stage": sid, **fields})
            return d["entries"][sid].copy()
        return self.change(update)

    def settle(self, sid, absence_verified, archive_verified, science_verified=False):
        if not absence_verified:
            raise GuardError("cannot release resource lock without confirmed VM/disk absence")
        if not archive_verified or not science_verified:
            raise GuardError("failed/partial/unverified science cannot become a settled stage")
        # Charge full reservation even if Spot was interrupted: no optimistic
        # refund until an independent cost reconciliation explicitly occurs.
        def update(d):
            e = d["entries"][sid]
            e.update(state="settled", charged_usd_upper=e["reserved_usd"],
                     archive_verified=True, science_verified=True, cleanup_verified_utc=stamp())
            d["events"].append({"utc": stamp(), "stage": sid, "event": "settled-at-full-reservation"})
        self.change(update)


def package_source(repo, commit, output):
    repo, output = Path(repo), Path(output)
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo, text=True)
    if actual != commit or dirty:
        raise GuardError("packaging requires exact clean source commit including untracked files")
    tracked = subprocess.check_output(["git", "ls-files"], cwd=repo, text=True).splitlines()
    files = [f for f in tracked if any(f == a or f.startswith(a + "/") for a in ALLOW)]
    if any((repo/f).is_symlink() for f in files):
        raise GuardError("packaged source cannot contain symlinks")
    if not {"run.sh", "uv.lock", "pyproject.toml"}.issubset(files):
        raise GuardError("source release lacks the shared executable entry")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "archive", "--format=tar", "-o", str(output), commit, "--", *files], cwd=repo, check=True)
    manifest = {"commit": commit, "source_files_sha256": {f: hashlib.sha256((repo / f).read_bytes()).hexdigest() for f in files}}
    atomic(output.with_suffix(".manifest.json"), manifest)
    return {"commit": commit, "archive_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "manifest_sha256": hashlib.sha256(output.with_suffix(".manifest.json").read_bytes()).hexdigest(), "files": len(files)}


def labels(c, s):
    return {"dams-task": digest(c["authorization_id"])[:24], "dams-stage": s["id"], "dams-source": c["source_commit"][:40]}


def create_command(c, s, e, zone, startup):
    return _create_command(c, s, e, zone, startup, compute_only=False)


def create_compute_only_command(c, s, e, zone, startup, *, startup_sha256):
    """Pure argv only; root pins/reviews PREPARE-ONLY startup before dispatch."""
    path = Path(startup)
    if (not isinstance(startup_sha256, str) or not re.fullmatch("[0-9a-f]{64}", startup_sha256)
            or path.is_symlink() or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != startup_sha256):
        raise GuardError("compute-only startup requires exact separately approved bytes")
    return _create_command(c, s, e, zone, startup, compute_only=True)


def _create_command(c, s, e, zone, startup, *, compute_only):
    caps = stage_capabilities(c, s)
    action = stage_termination_action(c, s, compute_only=compute_only)
    if zone not in c["zones"] or utc(e["termination_utc"]) > utc(c["global_deadline_utc"]):
        raise GuardError("create zone/absolute expiry differs from the frozen plan")
    performance = (["--boot-disk-provisioned-iops=3000", "--boot-disk-provisioned-throughput=140"]
                   if caps["disk_type"] == "hyperdisk-balanced" else [])
    credentials = (["--no-service-account", "--no-scopes"] if compute_only else
                   ["--service-account=" + c["service_account"], "--scopes=https://www.googleapis.com/auth/devstorage.read_write"])
    return ["gcloud", "compute", "instances", "create", e["instance_name"],
            "--project=" + c["project"], "--zone=" + zone, "--machine-type=" + s["machine_type"],
            "--provisioning-model=" + caps["purchase_mode"], "--instance-termination-action=" + action,
            "--termination-time=" + e["termination_utc"], "--maintenance-policy=TERMINATE", "--no-restart-on-failure",
            "--image=" + c["image"], "--boot-disk-type=" + caps["disk_type"], "--boot-disk-size=" + str(s.get("disk_gib", 50)) + "GB",
            "--boot-disk-interface=" + caps["disk_interface"], *performance, "--boot-disk-auto-delete",
            *credentials,
            "--network-interface=subnet=" + c["subnet"] + ",nic-type=GVNIC" + (",no-address" if c["network_mode"] == "internal-offline" else ",network-tier=PREMIUM"),
            "--metadata=enable-oslogin=TRUE,block-project-ssh-keys=TRUE,dams-source-commit=" + c["source_commit"] + ",dams-source-image-id=" + str(c["image_id"]),
            "--metadata-from-file=startup-script=" + str(startup),
            "--labels=" + ",".join(k + "=" + v for k, v in labels(c, s).items()), "--format=json", "--quiet"]


class Gcloud:
    def __init__(self, c, folder):
        self.c, self.folder = c, Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.seq = max((int(p.stem.split("-")[-1]) for p in self.folder.glob("command-*.json")), default=0)

    def run(self, args, timeout=120):
        args = [*args, "--configuration=" + self.c["gcloud_configuration"], "--account=" + self.c["gcloud_account"]]
        self.seq += 1
        path = self.folder / f"command-{self.seq:05}.json"
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            record = {"utc": stamp(), "argv": args, "exit": r.returncode, "stdout": r.stdout, "stderr": r.stderr}
        except subprocess.TimeoutExpired as e:
            record = {"utc": stamp(), "argv": args, "exit": None, "timeout": True,
                      "stdout": (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else e.stdout,
                      "stderr": (e.stderr or b"").decode() if isinstance(e.stderr, bytes) else e.stderr}
        atomic(path, record)
        return record

    def inventory(self, s=None):
        task = digest(self.c["authorization_id"])[:24]
        args = ["gcloud", "compute", "instances", "list", "--project=" + self.c["project"],
                "--format=json"]
        r = self.run(args)
        if r["exit"] != 0:
            raise GuardError("inventory failure: hold reservation; do not create another VM")
        data = json.loads(r["stdout"])
        if not isinstance(data, list):
            raise GuardError("invalid inventory")
        if any(vm.get("labels", {}).get("dams-task") != task for vm in data):
            raise GuardError("unmanaged/different-task VM in dedicated project; no additional VM")
        return data


def validate_instance(c, s, e, vm):
    return _validate_instance(c, s, e, vm, compute_only=False)


def validate_compute_only_instance(c, s, e, vm):
    """Explicit no-SA readback; never called by the legacy GCS execute path."""
    return _validate_instance(c, s, e, vm, compute_only=True)


def _validate_instance(c, s, e, vm, *, compute_only):
    caps = stage_capabilities(c, s)
    action = stage_termination_action(c, s, compute_only=compute_only)
    expected = labels(c, s)
    if vm.get("name") != e["instance_name"] or any(vm.get("labels", {}).get(k) != v for k, v in expected.items()):
        raise GuardError("existing VM does not match this frozen task/stage")
    scheduling = vm.get("scheduling", {})
    if vm["machineType"].split("/")[-1] != s["machine_type"] or scheduling.get("provisioningModel") != caps["purchase_mode"]:
        raise GuardError("existing VM differs from the frozen machine/purchase mode")
    if (scheduling.get("instanceTerminationAction") != action or not scheduling.get("terminationTime")
            or utc(scheduling["terminationTime"]) != utc(e["termination_utc"]) or scheduling.get("maxRunDuration")):
        raise GuardError("existing VM termination action/absolute time differs from the frozen plan")
    if caps["purchase_mode"] == "STANDARD" and (scheduling.get("automaticRestart") is not False or scheduling.get("onHostMaintenance") != "TERMINATE"):
        raise GuardError("Standard VM restart/maintenance policy differs from the bounded frozen plan")
    if vm.get("deletionProtection"):
        raise GuardError("deletion protection would block provider/explicit cleanup")
    if vm.get("zone", "").split("/")[-1] not in c["zones"]:
        raise GuardError("existing VM zone differs from the frozen candidate zones")
    accounts = vm.get("serviceAccounts", [])
    if compute_only:
        if accounts != []:
            raise GuardError("compute-only VM must have no attached service account or scopes")
    elif len(accounts) != 1 or accounts[0].get("email") != c["service_account"] or accounts[0].get("scopes") != ["https://www.googleapis.com/auth/devstorage.read_write"]:
        raise GuardError("existing VM has different/overbroad attached credentials")
    interfaces = vm.get("networkInterfaces", [])
    if len(interfaces) != 1 or interfaces[0].get("nicType") != "GVNIC" or interfaces[0].get("subnetwork", "").split("/")[-1] != c["subnet"].split("/")[-1]:
        raise GuardError("existing VM network/GVNIC differs from the frozen plan")
    if interfaces[0].get('stackType') not in (None,'IPV4_ONLY') or interfaces[0].get('ipv6AccessConfigs'):
        raise GuardError('unpriced/unfiltered IPv6 interface is outside this control profile')
    if c["network_mode"] == "internal-offline" and interfaces[0].get("accessConfigs"):
        raise GuardError("unexpected external IP on an internal-only VM")
    if c["network_mode"] == "iap-ephemeral-ip":
        external=interfaces[0].get('accessConfigs',[])
        if len(external)!=1 or external[0].get('type')!='ONE_TO_ONE_NAT' or external[0].get('networkTier')!='PREMIUM' or not external[0].get('natIP'):
            raise GuardError('ephemeral external IPv4/Premium path differs from the frozen plan')
    meta = {m["key"]: m["value"] for m in vm.get("metadata", {}).get("items", [])}
    if meta.get("dams-source-commit") != c["source_commit"] or meta.get("dams-source-image-id") != str(c["image_id"]) or meta.get("enable-oslogin", "").upper() != "TRUE" or meta.get("block-project-ssh-keys", "").upper() != "TRUE":
        raise GuardError("existing VM source/image/controlled access metadata differs")
    if len(vm.get("disks", [])) != 1 or not vm["disks"][0].get("boot") or not vm["disks"][0].get("autoDelete"):
        raise GuardError("unexpected unmanaged/retained extra disk")
    if caps["purchase_mode"] == "STANDARD" and vm["disks"][0].get("interface") != caps["disk_interface"]:
        raise GuardError("Standard boot disk must use the frozen NVMe interface")
    return vm


def verify_boot_disk(g, c, s, vm):
    caps = stage_capabilities(c, s)
    name = vm["disks"][0]["source"].split("/")[-1]
    r = g.run(["gcloud", "compute", "disks", "describe", name, "--project=" + c["project"],
               "--zone=" + vm["zone"].split("/")[-1], "--format=json"])
    if r["exit"] != 0:
        raise GuardError("boot disk image/configuration unverified")
    d = json.loads(r["stdout"])
    if str(d.get("sourceImageId")) != str(c["image_id"]) or d.get("sourceImage") != c["image"] or d.get("type", "").split("/")[-1] != caps["disk_type"] or int(d.get("sizeGb", 0)) != s.get("disk_gib", 50):
        raise GuardError("boot disk is not the fixed planned image/type/capacity")
    if caps["disk_type"] == "hyperdisk-balanced" and (int(d.get("provisionedIops", 0)) != 3000 or int(d.get("provisionedThroughput", 0)) != 140):
        raise GuardError("unexpected billable Hyperdisk performance provision")
    if caps["disk_type"] != "hyperdisk-balanced" and (d.get("provisionedIops") or d.get("provisionedThroughput")):
        raise GuardError("unexpected performance provision on the frozen Persistent Disk")


def quota_value(info, wanted):
    # Empty details are zero, not an approval; reconciling preferences are not grants.
    values = [money(x.get("details", {}).get("value", 0)) for x in info.get("dimensionsInfos", [])
              if x.get("dimensions", {}) == wanted]
    if len(values) != 1:
        raise GuardError("current quota dimension grant unavailable/ambiguous")
    return values[0]


def preflight_quotas(g, c, stage=None):
    # All-stage costs are reserved up front; quota is checked again before EACH
    # stage, allowing the bounded Linux trial while higher grants are pending.
    planned = [stage] if stage else c["stages"]
    targets = {}
    for s in planned:
        caps = stage_capabilities(c, s)
        key = "C4D_STANDARD" if caps["quota"]["dimensions"].get("vm_family") == "C4D" else "C4N_STANDARD" if caps["quota"]["dimensions"].get("vm_family") == "C4N" else caps["purchase_mode"]
        target = c["price_snapshot"]["machines"][s["machine_type"]]["vcpus"]
        if type(target) is not int or target < 1:
            raise GuardError("actual machine catalog CPU count is missing/invalid")
        targets[key] = max(targets.get(key, 0), target)
    # This profile requires an explicitly granted preemptible pool. Official
    # allocation rules say that after requesting it, applicable Spot resources
    # consume ONLY that pool. Standard C4D family quota is not an additional AND
    # requirement and cannot serve as an exhausted-Spot fallback.
    if "SPOT" in targets:
        r = g.run(["gcloud", "beta", "quotas", "info", "describe", "PREEMPTIBLE-CPUS-per-project-region",
                   "--service=compute.googleapis.com", "--project=" + c["project"], "--format=json"])
        if r["exit"] != 0 or quota_value(json.loads(r["stdout"]), {"region": c["region"]}) < targets["SPOT"]:
            raise GuardError("requested stage lacks granted Spot CPU pool; no standard-quota fallback")
        r = g.run(["gcloud", "compute", "regions", "describe", c["region"],
                   "--project=" + c["project"], "--format=json"])
        if r["exit"] != 0:
            raise GuardError("Spot regional quota usage is unverified")
        info = json.loads(r["stdout"])
        q = [x for x in info.get("quotas", []) if x.get("metric") == "PREEMPTIBLE_CPUS"]
        if (info.get("name") != c["region"] or len(q) != 1
                or money(q[0]["limit"]) - money(q[0]["usage"]) < targets["SPOT"]):
            raise GuardError("requested Spot stage lacks unused preemptible CPU headroom")
    if "STANDARD" in targets:
        r = g.run(["gcloud", "beta", "quotas", "info", "describe", "M3-CPUS-per-project-region",
                   "--service=compute.googleapis.com", "--project=" + c["project"], "--format=json"])
        info = json.loads(r["stdout"]) if r["exit"] == 0 else {}
        if (r["exit"] != 0 or info.get("quotaId") != "M3-CPUS-per-project-region"
                or info.get("metric") != "compute.googleapis.com/m3_cpus"
                or quota_value(info, {"region": c["region"]}) < targets["STANDARD"]):
            raise GuardError("requested Standard M3 stage lacks an effective regional M3 CPU grant")
        r = g.run(["gcloud", "compute", "regions", "describe", c["region"],
                   "--project=" + c["project"], "--format=json"])
        if r["exit"] != 0:
            raise GuardError("Standard M3 regional usage is unverified")
        info = json.loads(r["stdout"])
        q = [x for x in info.get("quotas", []) if x.get("metric") == "M3_CPUS"]
        if (info.get("name") != c["region"] or len(q) != 1
                or money(q[0]["limit"]) - money(q[0]["usage"]) < targets["STANDARD"]):
            raise GuardError("requested Standard M3 stage lacks unused M3 CPU quota; other pools cannot substitute")
    if "C4D_STANDARD" in targets:
        r = g.run(["gcloud", "beta", "quotas", "info", "describe", "CPUS-PER-VM-FAMILY-per-project-region",
                   "--service=compute.googleapis.com", "--project=" + c["project"], "--format=json"])
        info = json.loads(r["stdout"]) if r["exit"] == 0 else {}
        if (r["exit"] != 0 or info.get("quotaId") != "CPUS-PER-VM-FAMILY-per-project-region"
                or info.get("metric") != "compute.googleapis.com/cpus_per_vm_family"
                or quota_value(info, {"region": c["region"], "vm_family": "C4D"}) < targets["C4D_STANDARD"]):
            raise GuardError("requested Standard C4D stage lacks an effective exact regional family grant")
        # QuotaInfo is a grant, not usage. Until family usage is available,
        # this narrow adapter requires no current C4D instance/reservation in
        # the granted region. Other regions/families are accounted by actual
        # global quota headroom; they do not consume this regional grant.
        # Readbacks remain cooperative observations, not provider capacity.
        for resource in ("instances", "reservations", "operations"):
            r = g.run(["gcloud", "compute", resource, "list", "--project=" + c["project"], "--format=json"])
            data = json.loads(r["stdout"]) if r["exit"] == 0 else None
            if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                raise GuardError("C4D family usage census is missing or malformed")
            for row in data:
                if resource == "operations":
                    if row.get("status") == "DONE":
                        continue
                    target = row.get("targetLink", "")
                    if (not target or "/instances/" in target or "/reservations/" in target):
                        raise GuardError("pending compute operation leaves C4D family allocation unverified")
                    continue
                machine = (row.get("machineType") if resource == "instances" else
                           row.get("specificReservation", {}).get("instanceProperties", {}).get("machineType"))
                if not isinstance(machine, str) or not machine:
                    raise GuardError("C4D family census machine identity is unavailable")
                if machine.rsplit("/", 1)[-1].startswith("c4d-"):
                    zone = row.get("zone")
                    match = re.fullmatch(r"([a-z][a-z0-9-]*[0-9])-[a-z]", zone.rsplit("/", 1)[-1]) if isinstance(zone, str) else None
                    if match is None:
                        raise GuardError("C4D family census resource zone is unavailable/invalid")
                    if match.group(1) == c["region"]:
                        raise GuardError("existing regional C4D usage requires actual family headroom accounting; grant alone is insufficient")
    if "C4N_STANDARD" in targets:
        r = g.run(["gcloud", "beta", "quotas", "info", "describe", "CPUS-PER-VM-FAMILY-per-project-region",
                   "--service=compute.googleapis.com", "--project=" + c["project"], "--format=json"])
        info = json.loads(r["stdout"]) if r["exit"] == 0 else {}
        if (r["exit"] != 0 or info.get("quotaId") != "CPUS-PER-VM-FAMILY-per-project-region"
                or info.get("metric") != "compute.googleapis.com/cpus_per_vm_family"
                or quota_value(info, {"region": c["region"], "vm_family": "C4N"}) < targets["C4N_STANDARD"]):
            raise GuardError("requested Standard C4N stage lacks an effective exact regional family grant")
        # QuotaInfo is a grant, not usage. Until family usage is available,
        # this narrow adapter requires no current C4N instance/reservation in
        # the granted region. Other regions/families are accounted by actual
        # global quota headroom; they do not consume this regional grant.
        # Readbacks remain cooperative observations, not provider capacity.
        for resource in ("instances", "reservations", "operations"):
            r = g.run(["gcloud", "compute", resource, "list", "--project=" + c["project"], "--format=json"])
            data = json.loads(r["stdout"]) if r["exit"] == 0 else None
            if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                raise GuardError("C4N family usage census is missing or malformed")
            for row in data:
                if resource == "operations":
                    if row.get("status") == "DONE":
                        continue
                    target = row.get("targetLink", "")
                    if (not target or "/instances/" in target or "/reservations/" in target):
                        raise GuardError("pending compute operation leaves C4N family allocation unverified")
                    continue
                machine = (row.get("machineType") if resource == "instances" else
                           row.get("specificReservation", {}).get("instanceProperties", {}).get("machineType"))
                if not isinstance(machine, str) or not machine:
                    raise GuardError("C4N family census machine identity is unavailable")
                if machine.rsplit("/", 1)[-1].startswith("c4n-"):
                    zone = row.get("zone")
                    match = re.fullmatch(r"([a-z][a-z0-9-]*[0-9])-[a-z]", zone.rsplit("/", 1)[-1]) if isinstance(zone, str) else None
                    if match is None:
                        raise GuardError("C4N family census resource zone is unavailable/invalid")
                    if match.group(1) == c["region"]:
                        raise GuardError("existing regional C4N usage requires actual family headroom accounting; grant alone is insufficient")
    r = g.run(["gcloud", "compute", "project-info", "describe", "--project=" + c["project"], "--format=json"])
    if r["exit"] != 0:
        raise GuardError("global CPU quota unverified")
    q = [x for x in json.loads(r["stdout"]).get("quotas", []) if x["metric"] == "CPUS_ALL_REGIONS"]
    if len(q) != 1 or money(q[0]["limit"]) - money(q[0]["usage"]) < max(targets.values()):
        raise GuardError("requested stage lacks global CPU headroom")


def preflight_environment(g, c):
    """Read actual image, subnet and firewall bytes before the first paid write."""
    project = g.run(["gcloud", "projects", "describe", c["project"], "--project=" + c["project"], "--format=json"])
    if project["exit"] != 0:
        raise GuardError("dedicated project identity cannot be verified")
    identity = json.loads(project["stdout"])
    if identity.get("projectId") != c["project"] or identity.get("labels", {}).get("dams-task") != digest(c["authorization_id"])[:24]:
        raise GuardError("project identity/task label differs from the private authorization")
    bucket = g.run(["gcloud","storage","buckets","describe","gs://"+c["bucket"],"--raw","--project="+c["project"],"--format=json"])
    if bucket["exit"]!=0:raise GuardError("protected result bucket is unverified")
    b=json.loads(bucket["stdout"]);iam=b.get("iamConfiguration",{})
    if b.get("name")!=c["bucket"] or str(b.get("projectNumber"))!=str(identity.get("projectNumber")) or b.get("location","").lower()!=c["region"] or b.get("storageClass")!="STANDARD":
        raise GuardError("bucket project/region/storage class differs from the priced plan")
    if not iam.get("uniformBucketLevelAccess",{}).get("enabled") or iam.get("publicAccessPrevention")!="enforced" or b.get("versioning",{}).get("enabled") or b.get("retentionPolicy") or b.get("defaultEventBasedHold"):
        raise GuardError("result bucket lacks private uniform access or has unpriced retention/versioning")
    if str(b.get("softDeletePolicy",{}).get("retentionDurationSeconds",0))!="0":
        raise GuardError("temporary bucket soft-deletion retention must be explicitly disabled")
    policy=g.run(["gcloud","storage","buckets","get-iam-policy","gs://"+c["bucket"],"--project="+c["project"],"--format=json"])
    project_policy=g.run(["gcloud","projects","get-iam-policy",c["project"],"--project="+c["project"],"--format=json"])
    if policy["exit"]!=0 or project_policy["exit"]!=0:raise GuardError("restricted service-account IAM is unverified")
    bindings=json.loads(policy["stdout"]).get("bindings",[]);member="serviceAccount:"+c["service_account"]
    if any(any(m in ('allUsers','allAuthenticatedUsers') for m in row.get('members',[])) for row in bindings):
        raise GuardError("result bucket has a public principal")
    roles=[row['role'] for row in bindings if member in row.get('members',[])]
    if roles!=['roles/storage.objectUser'] or any(member in row.get('members',[]) for row in json.loads(project_policy['stdout']).get('bindings',[])):
        raise GuardError("guest service account requires bucket-only objectUser without project roles")
    image = g.run(["gcloud", "compute", "images", "describe", c["image"].split("/")[-1],
                   "--project=" + c["image"].split("/projects/")[1].split("/")[0], "--format=json"])
    if image["exit"] != 0:
        raise GuardError("fixed source image cannot be verified")
    im = json.loads(image["stdout"])
    if str(im.get("id")) != str(c["image_id"]) or im.get("selfLink") != c["image"] or im.get("status") != "READY" or im.get("architecture") != "X86_64":
        raise GuardError("fixed image ID/architecture/status differs from the frozen plan")
    if any(stage_capabilities(c, s)["purchase_mode"] == "STANDARD" for s in c["stages"]):
        features = {row.get("type") for row in im.get("guestOsFeatures", [])}
        if not {"GVNIC", "UEFI_COMPATIBLE"}.issubset(features):
            raise GuardError("fixed Standard image lacks required gVNIC/UEFI features; guest NVMe/driver trial remains necessary")
    subnet = g.run(["gcloud", "compute", "networks", "subnets", "describe", c["subnet"].split("/")[-1],
                    "--region=" + c["region"], "--project=" + c["project"], "--format=json"])
    if subnet["exit"] != 0:
        raise GuardError("subnetwork cannot be verified")
    net = json.loads(subnet["stdout"])
    if net.get("region", "").split("/")[-1] != c["region"] or not net.get("privateIpGoogleAccess"):
        raise GuardError("subnetwork region/private Google access differs from the plan")
    if net.get('stackType') not in (None,'IPV4_ONLY'):raise GuardError('this IAP/HTTPS firewall profile requires an IPv4-only subnet')
    if c["network_mode"] == "internal-offline":
        # A coordinator declaration alone does not certify image contents. A
        # separately executed image-build doctor receipt binds the locked stack.
        receipt = c.get("image_dependency_receipt", {})
        if receipt.get("image_id") != str(c["image_id"]) or receipt.get("uv_version") != "0.9.26" or receipt.get("python_version") != "3.14.2" or receipt.get("uv_lock_sha256") != hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest() or receipt.get("offline_prepare_exit") != 0:
            raise GuardError("internal-only mode lacks an executed, image/lock-bound offline dependency receipt")
    rules = g.run(["gcloud", "compute", "firewall-rules", "list", "--project=" + c["project"], "--format=json"])
    if rules["exit"] != 0:
        raise GuardError("firewall inventory is unverified")
    applicable = [r for r in json.loads(rules["stdout"]) if r.get("network") == net.get("network") and not r.get("disabled")
                  and not r.get("targetTags") and (not r.get("targetServiceAccounts") or c["service_account"] in r["targetServiceAccounts"])]
    desired = {( "INGRESS", "allow"): (["35.235.240.0/20"], [{"IPProtocol": "tcp", "ports": ["22"]}]),
               ("INGRESS", "deny"): (["0.0.0.0/0"], [{"IPProtocol": "all"}]),
               ("EGRESS", "allow"): (["0.0.0.0/0"], [{"IPProtocol": "tcp", "ports": ["443"]}]),
               ("EGRESS", "deny"): (["0.0.0.0/0"], [{"IPProtocol": "all"}])}
    seen = {}
    for r in applicable:
        action = "allow" if r.get("allowed") else "deny"
        key = (r.get("direction"), action)
        ranges = r.get("sourceRanges" if key[0] == "INGRESS" else "destinationRanges", [])
        if key not in desired or key in seen or (ranges, r.get("allowed" if action == "allow" else "denied")) != desired[key] or r.get("targetServiceAccounts") != [c["service_account"]]:
            raise GuardError("unexpected/broad firewall rule applies to the research service account")
        seen[key] = r["priority"]
    if set(seen) != set(desired) or any(seen[(d, "allow")] >= seen[(d, "deny")] for d in ("INGRESS", "EGRESS")):
        raise GuardError("IAP/HTTPS allow plus lower-priority deny-all rules are incomplete")
    # Hierarchical/org policies must be reviewed by the coordinator separately;
    # the actual small guest connectivity trial remains a prerequisite to scale.
    return {"image_id": str(im["id"]), "subnet": net["selfLink"], "firewall_rules": len(applicable),"protected_storage_verified":True,
            "network_mode": c["network_mode"], "observed_utc": stamp()}


def cloud_origin(c, s):
    return {"packaged_commit": c["source_commit"], "project_hash": hashlib.sha256(c["project"].encode()).hexdigest(),
            "task_hash": digest({"authorization_id": c["authorization_id"], "source_commit": c["source_commit"], "spec": s["spec"], "scale": s["scale"]}),
            "image_digest": digest({"selfLink": c["image"], "id": str(c["image_id"])}),
            "machine_type": s["machine_type"], "environment": "GCP"}


def reconcile_stage(g, c, s, e, ledger):
    """Resolve a reconnect/timeout without guessing that a missing reply failed."""
    live = g.inventory()
    if len(live) > 1:
        raise GuardError("multiple VMs remain: resource lock held")
    if live:
        return validate_instance(c, s, e, live[0])
    if e["state"] == "uncertain" and e["attempts"] == 0:
        if e.get("source_upload_attempts",0)>=c.get("max_create_attempts",2):
            raise GuardError("bounded source upload attempts exhausted; no compute request was issued")
        ledger.event(s["id"],state="reserved",reconciliation="no compute request ever issued and full project VM inventory empty")
        e["state"]="reserved"
        return None
    if e["state"] not in ("creating", "uncertain", "running"):
        return None
    r = g.run(["gcloud", "compute", "operations", "list", "--project=" + c["project"],
               "--filter=targetLink~instances/" + e["instance_name"] + "$", "--format=json"])
    if r["exit"] != 0:
        raise GuardError("operation outcome unverified; duplicate create prohibited")
    operations = json.loads(r["stdout"])
    if not operations or any(x.get("status") != "DONE" for x in operations):
        raise GuardError("absent/pending operation evidence cannot release ambiguous create")
    disks = g.run(["gcloud", "compute", "disks", "list", "--project=" + c["project"],
                   "--filter=name=" + e["instance_name"], "--format=json"])
    if disks["exit"] != 0 or json.loads(disks["stdout"]):
        raise GuardError("retained disk/unverified inventory prevents another VM")
    if e["attempts"] >= c.get("max_create_attempts", 2) or datetime.now(timezone.utc) + timedelta(seconds=120) >= utc(e["termination_utc"]):
        raise GuardError("retry/deadline allowance exhausted; retain failure and clean up")
    ledger.event(s["id"], state="reserved", reconciliation="DONE operations and VM/disk absence verified; same deadline/reservation")
    e["state"] = "reserved"
    return None


def startup_script(runtime, archive_uri, archive_sha, manifest_uri, manifest_sha):
    """Guest metadata-token download: no user's credentials enter the guest."""
    # Private runtime contains resource locations only, never budget or keys.
    payload = json.dumps(runtime, sort_keys=True)
    return "#!/bin/bash\nset -euo pipefail\numask 077\nmkdir -p /opt/dams /var/lib/dams\n" + "python3 - <<'DAMS_STARTUP_PY'\n" + f"""
import hashlib,json,pathlib,tarfile,urllib.request,urllib.parse
root=pathlib.Path('/opt/dams')
def download(uri,sha,dest):
    req=urllib.request.Request('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',headers={{'Metadata-Flavor':'Google'}})
    token=json.load(urllib.request.urlopen(req,timeout=20))['access_token']
    bucket,key=uri[5:].split('/',1)
    url='https://storage.googleapis.com/storage/v1/b/'+bucket+'/o/'+urllib.parse.quote(key,safe='')+'?alt=media'
    req=urllib.request.Request(url,headers={{'Authorization':'Bearer '+token}})
    h=hashlib.sha256()
    with urllib.request.urlopen(req,timeout=60) as r,dest.open('wb') as f:
        while chunk:=r.read(8*1024*1024): h.update(chunk);f.write(chunk)
    if h.hexdigest()!=sha: raise RuntimeError('source package checksum mismatch')
download({archive_uri!r},{archive_sha!r},root/'source.tar')
download({manifest_uri!r},{manifest_sha!r},root/'source-manifest.json')
with tarfile.open(root/'source.tar') as tar:
    for member in tar.getmembers():
        name=pathlib.PurePosixPath(member.name)
        if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()) or not (root/member.name).resolve().is_relative_to(root.resolve()):
            raise RuntimeError('unsafe source archive member')
    tar.extractall(root)
manifest=json.loads((root/'source-manifest.json').read_text())
for name,sha in manifest['source_files_sha256'].items():
    if hashlib.sha256((root/name).read_bytes()).hexdigest()!=sha: raise RuntimeError('source file checksum mismatch')
(pathlib.Path('/var/lib/dams')/'guest-runtime.json').write_text({payload!r})
""" + "DAMS_STARTUP_PY\ncd /opt/dams\n/bin/bash cloud/install-services.sh\n"


def execute(c, folder):
    """Explicitly authorized coordinator only. Fail closed on ambiguous creation."""
    if c.get("execution_mode") == "compute-only-iap":
        from research_tools.compute_only_entry import execute as compute_execute
        return compute_execute(checked_config(c), folder)
    if c.get('paid_actions_authorized') is not True:raise GuardError('execute requires explicit private paid-action authorization')
    c = checked_config(c)
    folder = Path(folder)
    with locked(folder / "coordinator.lock"):
        if (folder/'closeout-started.json').exists():raise GuardError('task closeout has started; no new paid execution is allowed')
        ledger, g = Ledger(folder / "budget-ledger.json", c), Gcloud(c, folder / "commands")
        atomic(folder / "environment-preflight.json", preflight_environment(g, c))
        package = package_source(ROOT, c["source_commit"], folder / "source.tar")
        chosen = c.get("execution_stage_ids", [s["id"] for s in c["stages"]])
        for s in c["stages"]:
            if s["id"] not in chosen:
                continue
            package_bytes=(folder/'source.tar').stat().st_size+(folder/'source.manifest.json').stat().st_size
            if package_bytes>int(money(s.get('source_package_gib_upper','0.01'))*1024**3):
                raise GuardError('fixed source archive/manifest exceed the explicitly reserved package storage')
            previous = [x["id"] for x in c["stages"][:c["stages"].index(s)]]
            saved = ledger.change(lambda d: d["entries"].copy())
            if any(saved.get(sid, {}).get("state") != "settled" or not saved.get(sid, {}).get("science_verified") for sid in previous):
                raise GuardError("earlier research stages must be scientifically verified before escalation")
            preflight_quotas(g, c, s)
            e = ledger.reserve(s)
            if e["state"] == "settled":
                if not e.get("archive_verified") or not e.get("science_verified"):
                    raise GuardError("legacy/unverified settlement cannot authorize escalation")
                from validate_pipeline import validate_pipeline_output
                validate_pipeline_output(folder / "results" / s["id"], s["spec"], s["scale"], expected_provenance=cloud_origin(c,s))
                continue
            if datetime.now(timezone.utc) >= utc(e["termination_utc"]):
                raise GuardError("stage absolute deadline expired; reconcile retained resources before resume")
            runtime = {"version": 1, "spec": s["spec"], "scale": s["scale"], "source_commit": c["source_commit"],
                       "project_hash": hashlib.sha256(c["project"].encode()).hexdigest(), "bucket": c["bucket"],
                       "prefix": "tasks/" + labels(c, s)["dams-task"] + "/" + s["id"],
                       "deadline_utc": e["termination_utc"], "shutdown_margin_seconds": s.get("shutdown_margin_seconds", 180),
                       "network_mode": c["network_mode"],
                       "upload_interval_seconds": s.get("upload_interval_seconds", 60),
                       "max_storage_requests":storage_request_allowance(c,s),
                       "max_upload_bytes": int(money(s["max_result_gib"]) * 1024**3), "runtime_limits": s["runtime_limits"]}
            runtime.update(stage_archive_options(s))
            runtime.update(stage_stop_options(c, s))
            if "purchase_mode" in s:
                runtime.update(purchase_mode=stage_capabilities(c, s)["purchase_mode"], machine_type=s["machine_type"],
                               expected_guest=s.get("expected_guest", {}))
            runtime["runtime_limits"] = {**runtime["runtime_limits"], **stage_science_deadlines(s, e["termination_utc"]),
                                         "provenance": cloud_origin(c,s)}
            base = "gs://" + c["bucket"] + "/packages/" + c["source_commit"]
            script = folder / (s["id"] + "-startup.sh")
            script.write_text(startup_script(runtime, base + "/source.tar", package["archive_sha256"],
                                            base + "/source.manifest.json", package["manifest_sha256"]))
            os.chmod(script, 0o600)
            vm = reconcile_stage(g, c, s, e, ledger)
            # Reservation/lock precede writes. Successful source staging is not
            # repeated on reconnect; partial staging has a separate finite cap.
            if not e.get("source_uploaded"):
                count=e.get("source_upload_attempts",0)
                if count>=c.get("max_create_attempts",2):raise GuardError("bounded source staging attempts exhausted")
                ledger.event(s["id"],source_upload_attempts=count+1)
                for local, remote in ((folder / "source.tar", base + "/source.tar"),
                                      (folder / "source.manifest.json", base + "/source.manifest.json")):
                    result=g.run(["gcloud","storage","cp",str(local),remote,"--project="+c["project"],"--quiet"])
                    if result["exit"]!=0:
                        ledger.event(s["id"],state="uncertain",failure="source upload incomplete")
                        raise GuardError("source upload incomplete; reservation retained")
                ledger.event(s["id"],source_uploaded=True,source_archive_sha256=package["archive_sha256"])
            if vm is None:
                if e["state"] in ("creating", "uncertain", "running"):
                    raise GuardError("ambiguous prior create/run: inventory alone cannot reset stage")
                for attempt in range(e["attempts"], c.get("max_create_attempts", 2)):
                    zone = c["zones"][attempt % len(c["zones"])]
                    ledger.event(s["id"], state="creating", attempts=attempt + 1, zone=zone)
                    result = g.run(create_command(c, s, e, zone, script), timeout=180)
                    live = g.inventory()
                    if len(live) == 1:
                        vm = validate_instance(c, s, e, live[0])
                        break
                    error = result.get("stderr") or ""
                    if result["exit"] is None or result["exit"] == 0 or len(live) != 0:
                        ledger.event(s["id"], state="uncertain", failure="ambiguous creation response")
                        raise GuardError("creation outcome ambiguous: no duplicate retry allowed")
                    if not any(x in error for x in ("ZONE_RESOURCE_POOL_EXHAUSTED", "RESOURCE_POOL_EXHAUSTED")):
                        ledger.event(s["id"], state="uncertain", failure="non-capacity creation error")
                        raise GuardError("creation failed; require coordinator reconciliation")
                    if datetime.now(timezone.utc) + timedelta(seconds=120) >= utc(e["termination_utc"]):
                        break
                    time.sleep(min(30, 5 * 2**attempt))
                if vm is None:
                    ledger.event(s["id"], state="uncertain", failure="bounded capacity attempts exhausted")
                    raise GuardError("capacity unavailable; no on-demand fallback")
            ledger.event(s["id"], state="running", instance_id=vm["id"], zone=vm["zone"].split("/")[-1])
            verify_boot_disk(g, c, s, vm)
            # VM-side completion and snapshot markers are verified by download.
            # Controller must never treat disappearance (Spot) as success.
            from cloud_worker import Store, download_snapshot
            store = Store(c["bucket"], runtime["prefix"], gcloud_project=c["project"], deadline=c["global_deadline_utc"],
                          gcloud_configuration=c["gcloud_configuration"], gcloud_account=c["gcloud_account"],
                          transfer_state=folder / (s["id"] + "-transfer-ledger.json"),
                          request_state=folder / (s["id"] + "-storage-requests.json"),max_requests=coordinator_request_allowance(c, s),
                          max_transfer_bytes=coordinator_transfer_allowance(c, s))
            result = None;marker=None
            while datetime.now(timezone.utc) < utc(e["termination_utc"]):
                try:
                    marker = store.get_json("terminal.json")
                    if marker and marker.get("source_commit") == c["source_commit"] and marker.get("instance_id") == vm["id"]:
                        descriptor=store.get_json('snapshots/'+marker['snapshot']+'.json')
                        if descriptor is None or digest(descriptor)!=marker['snapshot'] or digest(descriptor['files'])!=descriptor['inventory_sha256']:
                            raise GuardError('terminal snapshot identity is unverified')
                        # Release expensive compute before potentially very
                        # large download; final science acceptance follows.
                        result=descriptor
                        break
                except Exception as error:
                    atomic(folder / (s["id"] + "-last-download-error.json"), {"utc": stamp(), "error": str(error)})
                if not g.inventory():
                    break
                time.sleep(15)
            live = g.inventory()
            # Explicit task labels and instance identity checked before deletion.
            for item in live:
                validate_instance(c, s, e, item)
                deletion = g.run(["gcloud", "compute", "instances", "delete", item["name"],
                                  "--project=" + c["project"], "--zone=" + item["zone"].split("/")[-1], "--quiet"])
                if deletion["exit"] != 0:
                    raise GuardError("deletion unconfirmed; resource lock retained")
            disks = g.run(["gcloud", "compute", "disks", "list", "--project=" + c["project"],
                           "--filter=name=" + e["instance_name"], "--format=json"])
            absent = not g.inventory() and disks["exit"] == 0 and not json.loads(disks["stdout"])
            if not absent:
                raise GuardError("post-stage VM/disk absence unverified; resource lock retained")
            if result:
                result=download_snapshot(store,marker['snapshot'],folder/'results'/s['id'],
                                         **stage_archive_options(s), **stage_evidence_download_options(s))
            if not result or marker.get("exit_code") != 0:
                ledger.event(s["id"], state="uncertain", failure="stage interrupted/failed; resume requires completed operation/absence proof")
                raise GuardError("stage interrupted/failed or results not verified; no escalation")
            from validate_pipeline import validate_pipeline_output
            expected_origin = cloud_origin(c,s)
            science = validate_pipeline_output(folder / "results" / s["id"], s["spec"], s["scale"], expected_provenance=expected_origin)
            atomic(folder / (s["id"] + "-scientific-validation.json"), science)
            ledger.settle(s["id"], absent, True, science_verified=True)
        saved = ledger.change(lambda d: d["entries"].copy())
        atomic(folder / "execution-receipt.json", {"utc": stamp(), "selected_stage_ids": chosen,
               "selected_stages_complete": True, "completed_stage_ids": [sid for sid,e in saved.items() if e.get("state") == "settled" and e.get("science_verified")],
               "pending_stage_ids": [s["id"] for s in c["stages"] if saved.get(s["id"], {}).get("state") != "settled"],
               "scope": "Selected controller stages only; this is not completion of the global research task."})
        return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    for name in ("plan", "execute"):
        q = sub.add_parser(name)
        q.add_argument("--private-config", type=Path, required=True)
        q.add_argument("--state-dir", type=Path, required=name == "execute")
        q.add_argument("--spec")
        q.add_argument("--scale", type=int)
    q = sub.add_parser("package")
    q.add_argument("--commit", required=True)
    q.add_argument("--output", type=Path, required=True)
    a = p.parse_args(argv)
    try:
        if a.action == "package":
            print(json.dumps(package_source(ROOT, a.commit, a.output), indent=2))
            return 0
        if a.private_config.stat().st_mode & 0o077:
            raise GuardError("private configuration must have mode 0600")
        c = checked_config(json.loads(a.private_config.read_text()))
        if a.spec is not None and (c.get("requested_spec") != a.spec or c.get("requested_scale") != a.scale):
            raise GuardError("selected spec/scale differs from frozen private task plan")
        if c.get("execution_mode") == "compute-only-iap":
            from research_tools.compute_only_entry import Entry, execute as compute_execute
            if a.action == "plan":
                with Entry(c) as entry:
                    result = entry.plan()
            else:
                result = compute_execute(c, a.state_dir)
            print(json.dumps(result, indent=2))
            return 0
        if a.action == "plan":
            print(json.dumps({"mode": "local-plan-no-cloud-actions", "deadline_utc": c["global_deadline_utc"],
                              "stage_reservations_usd": {s["id"]: str(stage_cost(c, s)) for s in c["stages"]},
                              "total_usd_upper": str(money(c["prior_spend_usd"]) + money(c["reserve_usd"]) + sum(stage_cost(c, s) for s in c["stages"]))}, indent=2))
            return 0
        if c.get("paid_actions_authorized") is not True:
            raise GuardError("paid actions require explicit private authorization")
        return execute(c, a.state_dir)
    except (GuardError, KeyError, ValueError, OSError, subprocess.SubprocessError) as e:
        print("Cloud control refused: " + str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
