"""Spot guest runner and content-addressed, checksum-verified persistence.

Uses the attached service account (guest) or coordinator gcloud token (local).
Tokens/session URLs never enter logs. No VM management permissions are needed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from cloud_control import GuardError, atomic, digest, locked, stamp, utc


class Store:
    def __init__(self, bucket, prefix, gcloud_project=None, deadline=None, transfer_state=None, max_transfer_bytes=None,
                 gcloud_configuration=None, gcloud_account=None, request_state=None, max_requests=None):
        self.bucket, self.prefix = bucket, prefix.strip("/")
        self.project, self.deadline = gcloud_project, deadline
        self.gcloud_configuration, self.gcloud_account = gcloud_configuration, gcloud_account
        self.token, self.token_until = "", 0
        self.transfer_state, self.max_transfer_bytes = transfer_state, max_transfer_bytes
        self.request_state, self.max_requests = request_state, max_requests

    def check_deadline(self):
        if self.deadline and datetime.now(timezone.utc) >= utc(self.deadline):
            raise GuardError("remote persistence deadline expired")

    def charge_request(self):
        if self.request_state is None:return
        path=Path(self.request_state)
        with locked(path.with_suffix('.lock')):
            d=json.loads(path.read_text()) if path.exists() else {'requests_upper':0}
            if d['requests_upper']>=self.max_requests:raise GuardError('reserved storage HTTP request allowance exhausted')
            d['requests_upper']+=1
            atomic(path,d)

    def charge_transfer(self, count):
        if self.transfer_state is None:
            return
        path = Path(self.transfer_state)
        with locked(path.with_suffix(".lock")):
            d = json.loads(path.read_text()) if path.exists() else {"bytes_upper": 0}
            if d["bytes_upper"] + count > self.max_transfer_bytes:
                raise GuardError("cumulative download egress exceeds the reserved bound")
            d["bytes_upper"] += count
            atomic(path, d)

    def auth(self):
        if time.time() < self.token_until:
            return self.token
        if self.project:
            self.token = subprocess.check_output(["gcloud", "auth", "print-access-token", "--project=" + self.project,
                                                 "--configuration=" + self.gcloud_configuration, "--account=" + self.gcloud_account], text=True, timeout=30).strip()
            self.token_until = time.time() + 120
        else:
            req = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                                         headers={"Metadata-Flavor": "Google"})
            result = json.load(urllib.request.urlopen(req, timeout=15))
            self.token = result["access_token"]
            self.token_until = time.time() + min(120, result["expires_in"] - 30)
        return self.token

    def url(self, key, media=False):
        key = self.prefix + "/" + key
        return ("https://storage.googleapis.com/storage/v1/b/" + self.bucket + "/o/" +
                urllib.parse.quote(key, safe="") + ("?alt=media" if media else ""))

    def request(self, url, data=None, method=None, headers=None):
        self.check_deadline()
        self.charge_request()
        h = {"Authorization": "Bearer " + self.auth(), **(headers or {})}
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        return urllib.request.urlopen(req, timeout=45)

    def get_json(self, key):
        try:
            with self.request(self.url(key, media=True)) as r:
                # Bound JSON descriptors independently of raw streaming files.
                length = int(r.headers.get("Content-Length", 32 * 1024**2))
                if length > 32 * 1024**2:
                    raise GuardError("remote JSON descriptor exceeds the metadata bound")
                self.charge_transfer(length)
                raw = r.read(32 * 1024**2 + 1)
                if len(raw) > 32 * 1024**2:
                    raise GuardError("oversize remote JSON descriptor")
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def download(self, key, dest):
        h = hashlib.sha256()
        with self.request(self.url(key, media=True)) as r, Path(dest).open("wb") as f:
            length = r.headers.get("Content-Length")
            if length is not None:
                self.charge_transfer(int(length))  # reserve before reading response payload
            while block := r.read(8 * 1024**2):
                self.check_deadline()
                if length is None:
                    self.charge_transfer(len(block))
                h.update(block)
                f.write(block)
            f.flush()
            os.fsync(f.fileno())
        return h.hexdigest()

    def put_file(self, key, path, expected_sha, immutable=True):
        path = Path(path)
        # Idempotent key, never overwrite immutable raw observations.
        query = {"uploadType": "resumable", "name": self.prefix + "/" + key}
        if immutable:
            query["ifGenerationMatch"] = "0"
        url = "https://storage.googleapis.com/upload/storage/v1/b/" + self.bucket + "/o?" + urllib.parse.urlencode(query)
        meta = json.dumps({"metadata": {"sha256": expected_sha}}).encode()
        try:
            with self.request(url, data=meta, method="POST", headers={"Content-Type": "application/json", "X-Upload-Content-Length": str(path.stat().st_size)}) as r:
                session = r.headers["Location"]
        except urllib.error.HTTPError as e:
            if e.code != 412 or not immutable:
                raise
            # Existence is not enough: read and hash actual remote bytes.
            with tempfile.TemporaryDirectory() as tmp:
                if self.download(key, Path(tmp) / "verify") != expected_sha:
                    raise GuardError("existing remote object content differs")
            return
        length, position = path.stat().st_size, 0
        with path.open("rb") as f:
            while True:
                self.check_deadline()
                block = f.read(8 * 1024**2)
                if not block and length:
                    break
                end = position + len(block) - 1
                span = f"bytes {position}-{end}/{length}" if length else "bytes */0"
                try:
                    with self.request(session, data=block, method="PUT", headers={"Content-Length": str(len(block)), "Content-Range": span}) as r:
                        r.read()
                except urllib.error.HTTPError as e:
                    if e.code != 308:
                        raise
                position += len(block)
                if position == length:
                    break
        # SHA metadata is advisory. Verification hashes downloaded bytes.
        with tempfile.TemporaryDirectory() as tmp:
            if self.download(key, Path(tmp) / "verify") != expected_sha:
                raise GuardError("remote upload byte verification failed")

    def put_json(self, key, data, immutable=True):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "value.json"
            path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")
            self.put_file(key, path, hashlib.sha256(path.read_bytes()).hexdigest(), immutable)

    def reconcile_usage(self,state,max_bytes):
        """Count retained objects, including a prior ambiguous/orphan upload."""
        state.setdefault('metadata_sizes',{});state.setdefault('verified',list(state['uploaded']))
        prefix=self.prefix+'/';token=None;total=0
        while True:
            query={'prefix':prefix,'maxResults':'1000','fields':'items(name,size),nextPageToken'}
            if token:query['pageToken']=token
            url='https://storage.googleapis.com/storage/v1/b/'+self.bucket+'/o?'+urllib.parse.urlencode(query)
            with self.request(url) as response:
                data=json.loads(response.read(32*1024**2+1))
            for item in data.get('items',[]):
                key=item['name'][len(prefix):];size=int(item['size']);total+=size
                if total>max_bytes:raise GuardError('retained actual cloud objects exceed the cumulative storage allowance')
                if key.startswith('blobs/'):
                    sha=key.removeprefix('blobs/')
                    if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise GuardError('unexpected object in content-addressed storage')
                    state['uploaded'][sha]=size
                else:state['metadata_sizes'][key]=max(size,state['metadata_sizes'].get(key,0))
            token=data.get('nextPageToken')
            if not token:break
        state['bytes']=max(state['bytes'],total)
        return state


def stable_copy(source, dest):
    """Reject an in-flight rewrite; source outputs are atomically published."""
    before = source.stat()
    h = hashlib.sha256()
    with source.open("rb") as a, dest.open("wb") as b:
        while block := a.read(8 * 1024**2):
            h.update(block)
            b.write(block)
    after = source.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise GuardError("source file changed during snapshot")
    return h.hexdigest(), after.st_size


def snapshot(source, store, state_path, max_bytes):
    source, state_path = Path(source), Path(state_path)
    with locked(state_path.with_suffix(".lock")), locked(source.with_name(source.name+'-snapshot.lock')):
        state = json.loads(state_path.read_text()) if state_path.exists() else {"uploaded": {}, "bytes": 0}
        state.setdefault('verified',list(state['uploaded']))
        state.setdefault('metadata_sizes',{})
        inventory, checkpoint_indices = {}, {}
        with tempfile.TemporaryDirectory(prefix="dams-snapshot-") as tmp:
            for path in sorted(source.rglob("*")):
                relative = path.relative_to(source).as_posix()
                if path.is_symlink():
                    raise GuardError("symlinks cannot enter remote research snapshots")
                if not path.is_file() or path.name.startswith(".") or path.suffix in (".tmp", ".lock", ".download-part"):
                    continue
                copied = Path(tmp) / "current"
                sha, size = stable_copy(path, copied)
                if path.name == "checkpoint-index.json":
                    checkpoint_indices[relative] = json.loads(copied.read_text())
                if sha not in state["uploaded"]:
                    if state["bytes"] + size > max_bytes:
                        raise GuardError("cumulative immutable upload bytes exceed reserved storage")
                    state["uploaded"][sha] = size
                    state["bytes"] += size
                    atomic(state_path,state)  # reserve even an ambiguous completed upload
                if sha not in state['verified']:
                    store.put_file("blobs/" + sha, copied, sha)
                    state['verified'].append(sha)
                    atomic(state_path, state)
                inventory[relative] = {"sha256": sha, "bytes": size}
        # Checkpoint index and payload must describe the same atomic checkpoint.
        for name, index in checkpoint_indices.items():
            records = index.get("snapshots", [{"file": "checkpoint.json", "sha256": index.get("checkpoint_sha256", index.get("sha256"))}])
            if not records:
                raise GuardError("empty checkpoint index")
            for item in records:
                checkpoint = (Path(name).parent / item["file"]).as_posix()
                if Path(item["file"]).name != item["file"] or checkpoint not in inventory or item["sha256"] != inventory[checkpoint]["sha256"]:
                    raise GuardError("checkpoint/index hash mismatch; snapshot not published")
        descriptor = {"version": 1, "files": inventory, "inventory_sha256": digest(inventory)}
        if not inventory:raise GuardError('empty/partial restored trees cannot become the latest snapshot')
        sid = digest(descriptor)
        latest={'snapshot':sid,'utc':stamp()}
        reserve_metadata(state,'snapshots/'+sid+'.json',descriptor,max_bytes)
        reserve_metadata(state,'latest.json',latest,max_bytes)
        # The ledger contains its own reserved size; converge the small decimal
        # length change before writing any remote metadata object.
        for _ in range(8):
            previous=state['metadata_sizes'].get('upload-ledger.json',0)
            reserve_metadata(state,'upload-ledger.json',state,max_bytes)
            if state['metadata_sizes']['upload-ledger.json']==previous:break
        else:raise GuardError('upload ledger size accounting failed to converge')
        atomic(state_path,state)
        store.put_json("snapshots/" + sid + ".json", descriptor)
        store.put_json("upload-ledger.json", state, immutable=False)
        # Mutable pointer is only a convenience; callers retain immutable ID.
        store.put_json("latest.json", latest, immutable=False)
        return sid


def reserve_metadata(state,key,value,max_bytes):
    size=len((json.dumps(value,sort_keys=True,indent=2)+'\n').encode())
    old=state['metadata_sizes'].get(key,0)
    delta=max(0,size-old)
    if state['bytes']+delta>max_bytes:raise GuardError('snapshot/ledger/pointer metadata exceeds reserved total storage')
    state['bytes']+=delta
    state['metadata_sizes'][key]=max(old,size)


def publish_terminal(store,state_path,value,max_bytes):
    with locked(Path(state_path).with_suffix('.lock')):
        state=json.loads(Path(state_path).read_text())
        reserve_metadata(state,'terminal.json',value,max_bytes)
        for _ in range(8):
            old=state['metadata_sizes'].get('upload-ledger.json',0)
            reserve_metadata(state,'upload-ledger.json',state,max_bytes)
            if state['metadata_sizes']['upload-ledger.json']==old:break
        else:raise GuardError('terminal ledger size failed to converge')
        atomic(state_path,state)
        store.put_json('upload-ledger.json',state,immutable=False)
        store.put_json('terminal.json',value,immutable=False)


def download_snapshot(store, sid, destination):
    destination=Path(destination).resolve()
    with locked(destination.with_name(destination.name+'-snapshot.lock')):
        return _download_snapshot(store,sid,destination)


def _download_snapshot(store, sid, destination):
    descriptor = store.get_json("snapshots/" + sid + ".json")
    if descriptor is None or digest(descriptor) != sid or digest(descriptor["files"]) != descriptor["inventory_sha256"]:
        raise GuardError("snapshot descriptor does not match immutable identity")
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    # A restored tree is exactly the selected immutable snapshot. Move older
    # unlisted files into a sibling history directory, preserving evidence.
    stale = [p for p in destination.rglob("*") if p.is_file() and p.relative_to(destination).as_posix() not in descriptor["files"]]
    if stale:
        history = destination.parent / (destination.name + "-snapshot-history") / (sid + "-" + str(time.time_ns()))
        for old in stale:
            if old.is_symlink(): raise GuardError("symlink in prior restored output")
            target = history / old.relative_to(destination)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(old, target)
    for name, data in descriptor["files"].items():
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts:
            raise GuardError("invalid remote output path")
        target = destination / rel
        if target.is_symlink() or not target.resolve().is_relative_to(destination):
            raise GuardError("symlink in remote output destination")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".download-part")
        if store.download("blobs/" + data["sha256"], tmp) != data["sha256"] or tmp.stat().st_size != data["bytes"]:
            raise GuardError("downloaded research output checksum/size mismatch")
        os.replace(tmp, target)
    atomic(destination / "download-verification.json", {"utc": stamp(), "snapshot": sid, "files": len(descriptor["files"]), "all_bytes_verified": True})
    return descriptor


def final_snapshot(source, store, state_path, max_bytes, deadline):
    """Wait only for the independent periodic uploader, within the same expiry."""
    while True:
        try:
            return snapshot(source, store, state_path, max_bytes)
        except BlockingIOError:
            if time.time() + 1 >= utc(deadline).timestamp():
                raise GuardError("snapshot lock remained busy until the absolute deadline")
            time.sleep(1)


def guest_metadata():
    def read(key):
        req = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/instance/" + key, headers={"Metadata-Flavor": "Google"})
        return urllib.request.urlopen(req, timeout=10).read().decode()
    # No credentials, project name, IP, or account address in scientific output.
    topology = {}
    for key,args in {'summary':['lscpu','--json'], 'cpu_rows':['lscpu','--json','--extended=CPU,NODE,SOCKET,CORE,ONLINE']}.items():
        try:
            r=subprocess.run(args,capture_output=True,text=True,timeout=10)
            topology[key]={'exit_code':r.returncode,'data':json.loads(r.stdout) if r.returncode==0 else None}
        except (OSError,ValueError,subprocess.TimeoutExpired):topology[key]={'status':'unavailable'}
    numa={}
    for node in Path('/sys/devices/system/node').glob('node[0-9]*'):
        numa[node.name]={name:(node/name).read_text() for name in ('cpulist','distance','meminfo') if (node/name).is_file()}
    disks={}
    for disk in Path('/sys/block').glob('*'):
        if disk.name.startswith(('loop','ram')):continue
        disks[disk.name]={name:(disk/name).read_text().strip() for name in ('size','queue/rotational','queue/logical_block_size','queue/physical_block_size','queue/nr_requests','queue/max_sectors_kb') if (disk/name).is_file()}
    return {"instance_id": read("id"), "machine_type": read("machine-type").split("/")[-1],
            "zone": read("zone").split("/")[-1], "guest_cpu_count": os.cpu_count(),
            "guest_cpu_affinity":sorted(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else None,
            "guest_cpu_topology":topology, "guest_numa_topology":numa, "guest_disk_devices":disks,
            "guest_meminfo": Path("/proc/meminfo").read_text() if Path("/proc/meminfo").exists() else None}


def io_cpu_snapshot():
    return {name:(Path('/proc')/name).read_text() if (Path('/proc')/name).exists() else None for name in ('stat','diskstats')}


def run_guest(runtime, root, work):
    work.mkdir(parents=True, exist_ok=True)
    with locked(work / "pipeline.lock"):
        output = work / "output"
        store = Store(runtime["bucket"], runtime["prefix"], deadline=runtime["deadline_utc"],
                      request_state=work/'storage-requests.json',max_requests=runtime['max_storage_requests'])
        # The same guard excludes the periodic uploader for the ENTIRE restore,
        # including before destination files/partial downloads first appear.
        with locked(output.with_name(output.name+'-snapshot.lock')):
            remote=store.get_json('upload-ledger.json') or {'uploaded':{},'bytes':0}
            if remote['bytes']>runtime['max_upload_bytes']:raise GuardError('prior cumulative storage exceeds this task reservation')
            remote=store.reconcile_usage(remote,runtime['max_upload_bytes'])
            atomic(work/'upload-state.json',remote)
            latest=store.get_json('latest.json')
            if latest:
                temporary=Path(tempfile.mkdtemp(prefix='restored-output-',dir=work))
                download_snapshot(store,latest['snapshot'],temporary)
                if output.exists():
                    history=work/'output-restore-history';history.mkdir(exist_ok=True)
                    os.replace(output,history/str(time.time_ns()))
                os.replace(temporary,output)
            else:output.mkdir(exist_ok=True)
        provenance = guest_metadata()
        limits = runtime["runtime_limits"]
        limits["provenance"] = {**limits.get("provenance", {}), **{k: provenance[k] for k in ("instance_id", "machine_type", "zone")},
                                "environment": "GCP"}
        atomic(work / "runtime-limits.json", limits)
        counters_before=io_cpu_snapshot()
        atomic(output / "cloud-execution.json", {"source_commit": runtime["source_commit"], "start_utc": stamp(),
                                                "cpu_io_counters_before":counters_before, **provenance})
        env = {**os.environ, "DAMS_PACKAGED_COMMIT": runtime["source_commit"], "DAMS_SOURCE_MANIFEST": str(root / "source-manifest.json"),
               "DAMS_OFFLINE_DEPENDENCIES": "1" if runtime.get("network_mode") == "internal-offline" else "0"}
        command = [str(root / "run.sh"), "--spec", runtime["spec"], "--scale", str(runtime["scale"]), "--output", str(output), "--runtime-limits", str(work / "runtime-limits.json")]
        with (output / "pipeline.log").open("a") as log:
            p = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            atomic(work / "process.json", {"pid": p.pid, "start_utc": stamp(), "deadline_utc": runtime["deadline_utc"]})
            def stop(*_):
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGTERM)
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            stop_time = utc(runtime["deadline_utc"]).timestamp() - runtime.get("shutdown_margin_seconds", 90)
            while p.poll() is None:
                if time.time() >= stop_time:
                    stop()
                    try:
                        p.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(p.pid, signal.SIGKILL)
                    break
                time.sleep(2)
            code = p.wait()
        atomic(output / "cloud-terminal.json", {"source_commit": runtime["source_commit"], "exit_code": code, "end_utc": stamp(),
                                               "cpu_io_counters_after":io_cpu_snapshot(), **provenance})
        sid = final_snapshot(output, store, work / "upload-state.json", runtime["max_upload_bytes"], runtime["deadline_utc"])
        # Pointer is written only after all remote raw bytes are verified.
        publish_terminal(store,work/'upload-state.json', {"source_commit": runtime["source_commit"], "instance_id": provenance["instance_id"],
                                        "exit_code": code, "snapshot": sid, "utc": stamp()},runtime['max_upload_bytes'])
        return code


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("run", "upload", "watchdog"))
    p.add_argument("--runtime", type=Path, default=Path("/var/lib/dams/guest-runtime.json"))
    p.add_argument("--root", type=Path, default=Path("/opt/dams"))
    p.add_argument("--work", type=Path, default=Path("/var/lib/dams"))
    a = p.parse_args(argv)
    runtime = json.loads(a.runtime.read_text())
    if a.action == "run":
        return run_guest(runtime, a.root, a.work)
    if a.action == "upload":
        output = a.work / "output"
        if output.exists():
            snapshot(output, Store(runtime["bucket"], runtime["prefix"], deadline=runtime["deadline_utc"],
                                   request_state=a.work/'storage-requests.json',max_requests=runtime['max_storage_requests']),
                     a.work / "upload-state.json", runtime["max_upload_bytes"])
        return 0
    # Independent guest watchdog is supplemental to server terminationTime.
    while time.time() < utc(runtime["deadline_utc"]).timestamp() - runtime.get("shutdown_margin_seconds", 90):
        time.sleep(2)
    subprocess.run(["systemctl", "kill", "--signal=SIGTERM", "dams-pipeline.service"], check=False)
    # Do not synchronously block poweroff behind a very large checkpoint.
    subprocess.run(["systemctl", "start", "--no-block", "dams-upload.service"], timeout=15, check=False)
    remaining = utc(runtime["deadline_utc"]).timestamp() - time.time() - 30
    if remaining > 0:
        time.sleep(min(remaining, runtime.get("shutdown_margin_seconds", 180) - 30))
    subprocess.run(["systemctl", "poweroff"], check=False)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Do not print HTTP request/token/session objects on failures.
        print("Cloud guest persistence failed: " + type(error).__name__, file=__import__("sys").stderr)
        raise SystemExit(2)
