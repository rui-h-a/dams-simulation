"""Frozen-profile guest runner and checksum-verified persistence.

Uses the attached service account (guest) or coordinator gcloud token (local).
Tokens/session URLs never enter logs. No VM management permissions are needed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from cloud_control import GuardError, atomic, digest, locked, stamp, utc


class PersistenceError(GuardError):
    """Allowlisted failure facts; never retain a URL, token or response body."""
    def __init__(self, error, context):
        status = error.code if isinstance(error, urllib.error.HTTPError) else context.get("http_status")
        self.facts = {"utc": stamp(), "error_type": type(error).__name__,
                      "http_status": status if isinstance(status, int) and 100 <= status <= 599 else None,
                      "phase": context["phase"], "reason": context.get("reason", "request_failed"),
                      "object_sha256": context["object_sha256"], "object_bytes": context["object_bytes"],
                      "confirmed_offset": context["confirmed_offset"], "immutable": context["immutable"]}
        super().__init__("cloud object persistence failed")


def persistence_error_facts(error):
    """Report safe facts without formatting a possibly sensitive exception."""
    if isinstance(error, PersistenceError):
        return {"event": "cloud_persistence_failure", **error.facts}
    status = error.code if isinstance(error, urllib.error.HTTPError) else None
    if isinstance(error, urllib.error.HTTPError):
        error.close()
    return {"event": "cloud_persistence_failure", "utc": stamp(), "error_type": type(error).__name__,
            "http_status": status if isinstance(status, int) and 100 <= status <= 599 else None,
            "phase": "unknown", "object_sha256": None}


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
        try:
            return urllib.request.urlopen(req, timeout=45)
        except urllib.error.HTTPError as error:
            # An unclosed HTTPError can later emit a ResourceWarning containing
            # its sensitive URI/message. No caller needs its response body.
            error.close()
            raise

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
        context = {"phase": "prepare", "object_sha256": expected_sha if isinstance(expected_sha, str) and
                   re.fullmatch(r"[0-9a-f]{64}", expected_sha) else None,
                   "object_bytes": None, "confirmed_offset": 0, "immutable": bool(immutable)}
        try:
            if context["object_sha256"] is None:
                context["reason"] = "invalid_expected_sha256"
                raise GuardError("invalid expected object SHA-256")
            context["object_bytes"] = path.stat().st_size
            return self._put_file(key, path, expected_sha, immutable, context)
        except Exception as error:
            # Never format the HTTPError: it contains the resumable session URI.
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            raise PersistenceError(error, context) from None

    def _verify_file(self, key, expected_sha, length, context):
        context.update(phase="verify", http_status=None)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "verify"
            actual_sha = self.download(key, target)
            if actual_sha != expected_sha or target.stat().st_size != length:
                context["reason"] = "remote_byte_verification_mismatch"
                raise GuardError("remote object content or size differs")

    def _put_file(self, key, path, expected_sha, immutable, context):
        # Idempotent key, never overwrite immutable raw observations.
        length, position = context["object_bytes"], 0
        query = {"uploadType": "resumable", "name": self.prefix + "/" + key}
        if immutable:
            query["ifGenerationMatch"] = "0"
        url = "https://storage.googleapis.com/upload/storage/v1/b/" + self.bucket + "/o?" + urllib.parse.urlencode(query)
        meta = json.dumps({"metadata": {"sha256": expected_sha}}).encode()
        context["phase"] = "initiate"
        try:
            with self.request(url, data=meta, method="POST", headers={"Content-Type": "application/json", "X-Upload-Content-Length": str(length)}) as r:
                session = r.headers["Location"]
        except urllib.error.HTTPError as e:
            e.close()
            if e.code != 412 or not immutable:
                raise
            # Existence is not enough: read and hash actual remote bytes.
            self._verify_file(key, expected_sha, length, context)
            return
        with path.open("rb") as f:
            while True:
                context.update(phase="chunk", http_status=None)
                self.check_deadline()
                # Seek to the bytes the server persisted, not the bytes sent.
                f.seek(position)
                chunk_length = min(8 * 1024**2, length - position)
                block = f.read(chunk_length)
                if len(block) != chunk_length:
                    context["reason"] = "local_upload_file_changed"
                    raise GuardError("local upload file changed")
                end = position + len(block) - 1
                span = f"bytes {position}-{end}/{length}" if length else "bytes */0"
                try:
                    with self.request(session, data=block, method="PUT", headers={"Content-Length": str(len(block)), "Content-Range": span}) as r:
                        status, response_headers = getattr(r, "status", 200), r.headers
                except urllib.error.HTTPError as e:
                    e.close()
                    if e.code == 412 and immutable:
                        # A previous commit can have succeeded before its verify
                        # GET failed. Reconcile both initiation and commit 412s.
                        self._verify_file(key, expected_sha, length, context)
                        return
                    if e.code != 308:
                        raise
                    status, response_headers = e.code, e.headers
                context["http_status"] = status
                if status == 308:
                    persisted = re.fullmatch(r"bytes=0-([0-9]+)", response_headers.get("Range", ""))
                    if persisted is None:
                        context["reason"] = "invalid_persisted_range"
                        raise GuardError("missing or invalid persisted upload Range")
                    next_position = int(persisted.group(1)) + 1
                    if not position < next_position <= position + len(block):
                        context["reason"] = "invalid_persisted_offset"
                        raise GuardError("persisted upload offset is nonprogressing or outside the sent range")
                    context["confirmed_offset"] = next_position
                    if next_position == length:
                        context["reason"] = "incomplete_final_upload_response"
                        raise GuardError("308 cannot certify final upload completion")
                    position = next_position
                    continue
                if status not in (200, 201) or position + len(block) != length:
                    context["reason"] = "unexpected_upload_completion"
                    raise GuardError("unexpected or premature upload completion response")
                context["confirmed_offset"] = length
                break
        # SHA metadata is advisory. Verification hashes downloaded bytes.
        self._verify_file(key, expected_sha, length, context)

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


def snapshot(source, store, state_path, max_bytes, *, include_reaped_working=False):
    source, state_path = Path(source), Path(state_path)
    with locked(state_path.with_suffix(".lock")), locked(source.with_name(source.name+'-snapshot.lock')):
        state = json.loads(state_path.read_text()) if state_path.exists() else {"uploaded": {}, "bytes": 0}
        state.setdefault('verified',list(state['uploaded']))
        state.setdefault('metadata_sizes',{})
        inventory, checkpoint_indices, manifests, identities = {}, {}, {}, {}
        completed_working, completion_manifests, excluded_working, reaped_incomplete_working = {}, {}, [], []
        with tempfile.TemporaryDirectory(prefix="dams-snapshot-") as tmp:
            for path in sorted(source.rglob("*")):
                relative = path.relative_to(source).as_posix()
                if path.is_symlink():
                    raise GuardError("symlinks cannot enter remote research snapshots")
                if not path.is_file():
                    continue
                if path.name.endswith(('-wal', '-shm', '-journal')):
                    if include_reaped_working:
                        raise GuardError("SQLite journal sidecars cannot certify a completed snapshot")
                    continue
                known_hidden = (path.name in ('.pipeline.lock', '.longitudinal-working.sqlite')
                                and not any(part.startswith('.') for part in Path(relative).parts[:-1]))
                if any(part.startswith('.') for part in Path(relative).parts) and not known_hidden:
                    continue
                if path.name == '.longitudinal-working.sqlite':
                    manifest_path = path.parent / 'manifest.json'
                    manifest = None
                    if manifest_path.is_symlink():
                        raise GuardError("symlinked working database completion manifest")
                    if manifest_path.is_file():
                        manifest_copy = Path(tmp) / 'working-completion-manifest'
                        manifest_sha, _ = stable_copy(manifest_path, manifest_copy)
                        manifest = json.loads(manifest_copy.read_text())
                    if manifest is not None and manifest.get('status') == 'complete':
                        expected_sha = manifest.get('output_sha256', {}).get(path.name)
                        if not isinstance(expected_sha, str) or re.fullmatch('[0-9a-f]{64}', expected_sha) is None:
                            raise GuardError("complete case does not bind its working database checksum")
                        completed_working[relative] = expected_sha
                        completion_manifests[manifest_path.relative_to(source).as_posix()] = manifest_sha
                    elif not include_reaped_working:
                        excluded_working.append(relative)
                        continue  # Never copy an active or incomplete SQLite writer's working database.
                    else:
                        reaped_incomplete_working.append(relative)
                if path.name != '.pipeline.lock' and path.suffix in (".tmp", ".lock", ".download-part"):
                    continue
                copied = Path(tmp) / "current"
                sha, size = stable_copy(path, copied)
                if relative in completed_working and sha != completed_working[relative]:
                    raise GuardError("completed working database differs from its manifest checksum")
                info = path.stat()
                identities[relative] = (info.st_ino, info.st_size, info.st_mtime_ns)
                if path.name == "checkpoint-index.json":
                    checkpoint_indices[relative] = json.loads(copied.read_text())
                if path.name.endswith('manifest.json'):
                    manifests[relative] = json.loads(copied.read_text())
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
        for name, sha in completion_manifests.items():
            if inventory.get(name, {}).get('sha256') != sha:
                raise GuardError("working database completion manifest changed before snapshot publication")
        for name, manifest in manifests.items():
            for filename, sha in manifest.get('output_sha256', {}).items():
                candidate = (source / Path(name).parent / filename).resolve()
                if not candidate.is_relative_to(source.resolve()):
                    raise GuardError("manifest output path escapes the research snapshot")
                relative = candidate.relative_to(source.resolve()).as_posix()
                if any(part.startswith('.') for part in Path(relative).parts):
                    if (candidate.name not in ('.pipeline.lock', '.longitudinal-working.sqlite')
                            or any(part.startswith('.') for part in Path(relative).parts[:-1])):
                        raise GuardError("unsupported required hidden output; snapshot not published")
                    if (candidate.name == '.longitudinal-working.sqlite' and relative in excluded_working
                            and manifest.get('status') != 'complete'):
                        continue
                if relative not in inventory or inventory[relative]['sha256'] != sha:
                    raise GuardError("manifest/output hash mismatch; snapshot not published")
        # Checkpoint index and payload must describe the same atomic checkpoint.
        for name, index in checkpoint_indices.items():
            records = index.get("snapshots", [{"file": "checkpoint.json", "sha256": index.get("checkpoint_sha256", index.get("sha256"))}])
            if not records:
                raise GuardError("empty checkpoint index")
            for item in records:
                checkpoint = (Path(name).parent / item["file"]).as_posix()
                if Path(item["file"]).name != item["file"] or checkpoint not in inventory or item["sha256"] != inventory[checkpoint]["sha256"]:
                    raise GuardError("checkpoint/index hash mismatch; snapshot not published")
                if 'files' in item:
                    components = item['files']
                    if (not isinstance(components, list) or len(components) != 2
                            or {c.get('file') for c in components} != {item['file'], str(Path(item['file']).with_suffix('.sqlite'))}):
                        raise GuardError("checkpoint group is incomplete or has an unexpected sidecar")
                    for component in components:
                        member = (Path(name).parent / component['file']).as_posix()
                        if inventory.get(member) != {'sha256': component.get('sha256'), 'bytes': component.get('bytes')}:
                            raise GuardError("checkpoint group checksum/size mismatch; snapshot not published")
        # Reject rewrites after copying too, including before metadata publication.
        for relative, identity in identities.items():
            path = source / relative
            info = path.stat()
            if path.is_symlink() or identity != (info.st_ino, info.st_size, info.st_mtime_ns):
                raise GuardError("source file changed before snapshot publication")
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != inventory[relative]['sha256']:
                    raise GuardError("source hash changed before snapshot publication")
            after = path.stat()
            if path.is_symlink() or identity != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise GuardError("source file changed during snapshot recheck")
        descriptor = {"version": 1, "files": inventory, "inventory_sha256": digest(inventory)}
        if completed_working or excluded_working or any(Path(name).name == '.longitudinal-working.sqlite' for name in inventory):
            descriptor['working_databases'] = {'complete_manifest_bound': sorted(completed_working),
                                               'include_after_pipeline_reaped': include_reaped_working,
                                               'included_uncompleted_after_reap': sorted(reaped_incomplete_working),
                                               'excluded_active_or_uncompleted': sorted(excluded_working)}
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
    """Snapshot only after the pipeline child has been reaped; writers are gone."""
    while True:
        try:
            return snapshot(source, store, state_path, max_bytes, include_reaped_working=True)
        except BlockingIOError:
            if time.time() + 1 >= utc(deadline).timestamp():
                raise GuardError("snapshot lock remained busy until the absolute deadline")
            time.sleep(1)


def instance_metadata(key):
    req = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/instance/" + key,
                                 headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(req, timeout=10) as response:
        value = response.read(8193)
    if len(value) > 8192:
        raise GuardError("instance metadata exceeds the bounded hardware receipt")
    return value.decode().strip()


def guest_memory(meminfo):
    if meminfo is not None:
        matches = re.findall(r'^MemTotal:\s+(\d+)\s+kB\s*$', meminfo, re.MULTILINE)
        if len(matches) != 1 or int(matches[0]) <= 0:
            raise GuardError("Linux MemTotal is unavailable or malformed")
        return int(matches[0]) * 1024, 'Linux MemTotal'
    try:
        pages, page_size = os.sysconf('SC_PHYS_PAGES'), os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError):
        raise GuardError("guest physical memory measurement is unavailable") from None
    if type(pages) is not int or type(page_size) is not int or pages <= 0 or page_size <= 0:
        raise GuardError("guest physical memory measurement is invalid")
    return pages * page_size, 'sysconf physical pages'


def root_block_interface(sys_dev_block=Path('/sys/dev/block'), root_device=None):
    """Resolve root's block device, partitions and all device-mapper slaves."""
    device = os.stat('/').st_dev if root_device is None else root_device
    key = str(os.major(device)) + ':' + str(os.minor(device))
    visited, namespaces = set(), set()

    def resolve(node):
        try:
            node = node.resolve()
        except (OSError, RuntimeError):
            return False
        if not node.exists() or node in visited or len(visited) >= 64:
            return False
        visited.add(node)
        slaves = list((node / 'slaves').glob('*'))
        if slaves:
            return all([resolve(slave) for slave in slaves])
        # A namespace and its NVMe controller must both occur in sysfs ancestry.
        names = [part.name for part in (node, *node.parents)]
        namespace = next((name for name in names if re.fullmatch(r'nvme\d+n\d+', name)), None)
        if namespace and any(re.fullmatch(r'nvme\d+', name) for name in names):
            namespaces.add(namespace)
            return True
        return False

    verified = resolve(Path(sys_dev_block) / key)
    return {'root_device': key, 'interface': 'NVME' if verified else 'unverified',
            'nvme_namespaces': sorted(namespaces) if verified else []}


def verify_guest(runtime, measurements):
    """Bind a private frozen profile to actual guest hardware before execution."""
    mode = runtime.get('purchase_mode', 'SPOT')
    machine = runtime.get('machine_type', runtime.get('runtime_limits', {}).get('provenance', {}).get('machine_type'))
    scheduling = measurements.get('guest_scheduling', {})
    if mode not in ('SPOT', 'STANDARD') or scheduling.get('preemptible') != ('TRUE' if mode == 'SPOT' else 'FALSE'):
        raise GuardError("guest scheduling differs from the frozen purchase mode")
    if machine is None or measurements.get('machine_type') != machine:
        raise GuardError("guest machine type differs from the frozen machine")
    if mode == 'SPOT' and machine not in ('c4d-highmem-4', 'c4d-highmem-96', 'c4d-highmem-192', 'c4d-highmem-384'):
        raise GuardError("unsupported frozen Spot guest profile")
    expected = runtime.get('expected_guest', {})
    receipt = {'version': 1, 'purchase_mode': mode, 'machine_type': machine,
               'mode_evidence': 'instance scheduling/preemptible; exact provisioningModel requires coordinator readback',
               'hardware_frozen_verified': False}
    if not expected and mode == 'SPOT':
        return receipt  # Historical Spot configs did not freeze a hardware margin.
    required = {'architecture', 'vcpus', 'memory_gib_min', 'memory_gib_max'}
    if (not isinstance(expected, dict) or set(expected) != required or expected['architecture'] != 'x86_64'
            or type(expected['vcpus']) is not int or expected['vcpus'] <= 0):
        raise GuardError("frozen guest CPU/architecture expectations are incomplete")
    if mode == 'STANDARD' and (machine not in ('m3-ultramem-32', 'm3-ultramem-64', 'm3-ultramem-128')
                               or expected['vcpus'] != int(machine.rsplit('-', 1)[-1])):
        raise GuardError("unsupported or inconsistent frozen Standard guest profile")
    if scheduling.get('automatic_restart') != 'FALSE' or scheduling.get('on_host_maintenance') != 'TERMINATE':
        raise GuardError("guest scheduling does not match the frozen deletion lifecycle")
    if measurements.get('guest_architecture') != expected['architecture']:
        raise GuardError("guest architecture differs from the frozen architecture")
    if type(measurements.get('guest_cpu_count')) is not int or measurements['guest_cpu_count'] != expected['vcpus']:
        raise GuardError("guest CPU count differs from the frozen vCPU count")
    affinity = measurements.get('guest_cpu_affinity')
    if (not isinstance(affinity, list) or any(type(cpu) is not int or not 0 <= cpu < expected['vcpus'] for cpu in affinity)
            or len(set(affinity)) != expected['vcpus'] or len(affinity) != expected['vcpus']):
        raise GuardError("guest CPU affinity excludes frozen vCPUs")
    try:
        minimum, maximum = Decimal(str(expected['memory_gib_min'])), Decimal(str(expected['memory_gib_max']))
    except (InvalidOperation, ValueError):
        raise GuardError("frozen memory range is invalid") from None
    memory = measurements.get('guest_memory_total_bytes')
    if (not minimum.is_finite() or not maximum.is_finite() or not 0 < minimum < maximum
            or type(memory) is not int or not minimum * 2**30 <= memory <= maximum * 2**30):
        raise GuardError("measured guest memory is outside the frozen usable range")
    if (measurements.get('guest_boot_disk_metadata_interface') != 'NVME'
            or measurements.get('guest_root_block', {}).get('interface') != 'NVME'):
        raise GuardError("guest root block device is not verified as NVMe")
    return {**receipt, 'hardware_frozen_verified': True, 'expected_guest': expected,
            'guest_architecture': measurements['guest_architecture'], 'guest_cpu_count': measurements['guest_cpu_count'],
            'guest_cpu_affinity_count': len(affinity), 'guest_memory_total_bytes': memory,
            'guest_memory_total_source': measurements['guest_memory_total_source'],
            'guest_root_block': measurements['guest_root_block']}


def guest_metadata():
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
    meminfo = Path('/proc/meminfo').read_text() if Path('/proc/meminfo').exists() else None
    memory, memory_source = guest_memory(meminfo)
    return {"instance_id": instance_metadata("id"), "machine_type": instance_metadata("machine-type").split("/")[-1],
            "zone": instance_metadata("zone").split("/")[-1], "guest_cpu_count": os.cpu_count(),
            "guest_architecture": platform.machine(),
            "guest_scheduling": {'preemptible': instance_metadata('scheduling/preemptible').upper(),
                                 'automatic_restart': instance_metadata('scheduling/automatic-restart').upper(),
                                 'on_host_maintenance': instance_metadata('scheduling/on-host-maintenance').upper()},
            "guest_boot_disk_metadata_interface": instance_metadata('disks/0/interface').upper(),
            "guest_root_block": root_block_interface(),
            "guest_memory_total_bytes": memory, "guest_memory_total_source": memory_source,
            "guest_cpu_affinity":sorted(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else None,
            "guest_cpu_topology":topology, "guest_numa_topology":numa, "guest_disk_devices":disks,
            "guest_meminfo": meminfo}


def io_cpu_snapshot():
    return {name:(Path('/proc')/name).read_text() if (Path('/proc')/name).exists() else None for name in ('stat','diskstats')}


def wait_pipeline_group_absent(pid, deadline, max_wait_seconds=5):
    """Read-only check of the pipeline's own start_new_session process group."""
    if type(pid) is not int or pid <= 0:
        raise GuardError("invalid owned pipeline process group")
    until = time.monotonic() + max_wait_seconds
    expiry = utc(deadline).timestamp()
    while True:
        try:
            os.killpg(pid, 0)  # Signal zero only probes existence/permission.
        except ProcessLookupError:
            return {'owned_pipeline_group_id': pid, 'owned_pipeline_group_absent': True,
                    'absence_checked_utc': stamp(), 'check': 'killpg(group_id, 0) returned ESRCH'}
        except (PermissionError, OSError):
            raise GuardError("cannot verify owned pipeline process group absence") from None
        remaining = min(until - time.monotonic(), expiry - time.time())
        if remaining <= 0:
            raise GuardError("owned pipeline process group still exists; final raw snapshot refused")
        time.sleep(min(.1, remaining))


def run_guest(runtime, root, work):
    work.mkdir(parents=True, exist_ok=True)
    with locked(work / "pipeline.lock"):
        provenance = guest_metadata()
        verification = verify_guest(runtime, provenance)
        atomic(work / 'guest-hardware-verification.json', verification)
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
        limits = runtime["runtime_limits"]
        limits["provenance"] = {**limits.get("provenance", {}), **{k: provenance[k] for k in ("instance_id", "machine_type", "zone")},
                                "environment": "GCP"}
        atomic(work / "runtime-limits.json", limits)
        counters_before=io_cpu_snapshot()
        atomic(output / "cloud-execution.json", {"source_commit": runtime["source_commit"], "start_utc": stamp(),
                                                "cpu_io_counters_before":counters_before,
                                                "guest_hardware_verification": verification, **provenance})
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
        group_verification = wait_pipeline_group_absent(p.pid, runtime['deadline_utc'])
        atomic(work / 'process-group-verification.json', group_verification)
        atomic(output / "cloud-terminal.json", {"source_commit": runtime["source_commit"], "exit_code": code, "end_utc": stamp(),
                                               "cpu_io_counters_after":io_cpu_snapshot(),
                                               "guest_hardware_verification": verification,
                                               "owned_pipeline_group_verification": group_verification, **provenance})
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
        print(json.dumps(persistence_error_facts(error), sort_keys=True), file=__import__("sys").stderr)
        raise SystemExit(2)
