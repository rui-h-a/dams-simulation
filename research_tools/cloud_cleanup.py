"""Dedicated-project closeout after two verified local research archives.

Only an explicitly authorized coordinator invokes --execute. The guest cannot
delete the project. Project billing is detached, never the shared billing account.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path
from cloud_control import GuardError, Gcloud, atomic, digest, locked, stamp, cloud_origin, money


def verify_archives(receipt,c=None):
    inventory = receipt["files_sha256"]
    roots = [Path(p).resolve() for p in receipt["archive_roots"]]
    studies=receipt.get('pipeline_roots',[])
    abort=receipt.get('closure_mode','complete')=='abort'
    if receipt.get('closure_mode','complete') not in ('complete','abort'):raise GuardError('invalid explicit closeout mode')
    if abort and (not c or c.get('abort_cleanup_authorized') is not True):raise GuardError('incomplete cleanup requires explicit private abort authorization')
    if len(set(roots)) < 2 or not inventory or (not studies and not abort):
        raise GuardError("two distinct complete archives with a nonempty inventory are required")
    if c and not abort:
        required={s['id'] for s in c['stages'] if not s.get('conditional',False)}
        actual={s.get('stage_id') for s in studies}
        if not required.issubset(actual) or not actual.issubset({s['id'] for s in c['stages']}) or len(actual)!=len(studies):
            raise GuardError('archive study roster differs from all required private-plan stages')
    from research_tools.validate_pipeline import validate_pipeline_output
    scientific=[]
    for root in roots:
        actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
        if actual!=set(inventory):raise GuardError('archive receipt is a subset or has extra/unlisted files')
        for name, sha in inventory.items():
            p = Path(name)
            if p.is_absolute() or ".." in p.parts:
                raise GuardError("invalid archive-relative path")
            f = root / p
            if f.is_symlink() or not f.is_file():
                raise GuardError("archive file missing or symlinked")
            h = hashlib.sha256()
            with f.open("rb") as stream:
                while block := stream.read(8 * 1024**2): h.update(block)
            if h.hexdigest() != sha:
                raise GuardError("archive byte checksum mismatch")
        for study in studies:
            rel=Path(study['path'])
            if rel.is_absolute() or '..' in rel.parts:raise GuardError('invalid archive scientific root')
            if not (root/rel).resolve().is_relative_to(root):raise GuardError('scientific archive root escapes the verified copy')
            stage=next((s for s in c['stages'] if s['id']==study['stage_id']),None) if c else None
            try:
                result=validate_pipeline_output(root/rel,stage['spec'] if stage else study['spec'],stage['scale'] if stage else study['scale'],
                                                expected_provenance=cloud_origin(c,stage) if stage else None)
            except (ValueError,OSError) as error:
                if abort:
                    scientific.append({'stage_id':study.get('stage_id'),'status':'incomplete','failure_type':type(error).__name__})
                    continue
                raise GuardError('archive does not contain a complete verified scientific pipeline') from error
            scientific.append({'stage_id':study.get('stage_id'),'unique_complete_cases':result['unique_complete_cases'],
                               'spec_sha256':result['spec_sha256'],'case_origins_sha256':result['case_origins_sha256']})
    return {"verified_utc": stamp(), "inventory_sha256": digest(inventory), "files": len(inventory), "copies": len(roots),'scientific_validation':scientific,
            'closure_mode':'abort' if abort else 'complete','science_complete':not abort}


def verify_retained_cloud_objects(c,folder,receipt,buckets,allow_deleted_archived=False):
    """Bind an abort archive to ALL currently retained dedicated-bucket objects.

    Byte SHA checks already cover both copies. Actual provider MD5/size bind
    those files to the observed object generations, including opaque partial
    uploads; MD5 is used for provider equality, not as an authenticity signature.
    """
    from cloud_worker import Store
    import urllib.parse
    maximum=c.get('cleanup_max_storage_requests',10000)
    metadata_bytes=c.get('cleanup_max_metadata_bytes',256*1024**2)
    cost=money(maximum)*(money(c['price_snapshot']['gcs_class_a_per_1000'])/1000)+money(metadata_bytes)/1024**3*money(c['price_snapshot']['egress_gib'])
    if cost>money(c['reserve_usd']):raise GuardError('cleanup inventory allowance exceeds the unchanged cleanup reserve')
    expected=receipt.get('cloud_object_files')
    if not isinstance(expected,dict):raise GuardError('abort archive requires an explicit retained-cloud-object map, including an empty map')
    observed={};first=Path(receipt['archive_roots'][0]).resolve()
    for bucket in buckets:
        name=bucket.get('name',bucket.get('id')).removeprefix('gs://').rstrip('/')
        store=Store(name,'',gcloud_project=c['project'],gcloud_configuration=c['gcloud_configuration'],gcloud_account=c['gcloud_account'],
                    request_state=Path(folder)/'cleanup-storage-requests.json',max_requests=maximum,
                    transfer_state=Path(folder)/'cleanup-metadata-transfer.json',max_transfer_bytes=metadata_bytes)
        token=None
        while True:
            query={'versions':'true','maxResults':'1000','fields':'items(name,size,generation,md5Hash),nextPageToken'}
            if token:query['pageToken']=token
            url='https://storage.googleapis.com/storage/v1/b/'+name+'/o?'+urllib.parse.urlencode(query)
            with store.request(url) as response:
                raw=response.read(32*1024**2+1);store.charge_transfer(len(raw))
                if len(raw)>32*1024**2:raise GuardError('cleanup object inventory response exceeds metadata bound')
                data=json.loads(raw)
            for item in data.get('items',[]):
                key='gs://'+name+'/'+item['name']+'#'+item['generation']
                if key not in expected:raise GuardError('retained cloud object is absent from both verified archives')
                rel=expected[key]
                if rel not in receipt['files_sha256']:raise GuardError('object archive mapping escapes the complete byte inventory')
                local=first/rel;md5=hashlib.md5(usedforsecurity=False)
                with local.open('rb') as f:
                    while block:=f.read(8*1024**2):md5.update(block)
                if local.stat().st_size!=int(item['size']) or base64.b64encode(md5.digest()).decode()!=item.get('md5Hash'):
                    raise GuardError('provider object generation differs from actual archived bytes')
                observed[key]=item['size']
            token=data.get('nextPageToken')
            if not token:break
    if not allow_deleted_archived and set(observed)!=set(expected):raise GuardError('retained-object map differs from the actual provider roster')
    return {'object_generations':len(observed),'roster_sha256':digest(observed),'all_actual_retained_objects_archived':True,
            'verified_object_uris':sorted(observed)}


def inventory_commands(c):
    project, region = "--project=" + c["project"], "--region=" + c["region"]
    # Disks, IPs, routers, schedules and caches can charge after a VM stops.
    families = {"instances": ["compute", "instances"], "disks": ["compute", "disks"],
                "snapshots": ["compute", "snapshots"], "images": ["compute", "images"],
                "addresses": ["compute", "addresses"], "forwarding_rules": ["compute", "forwarding-rules"],
                "routers": ["compute", "routers"], "firewalls": ["compute", "firewall-rules"],
                "subnets": ["compute", "networks", "subnets"], "networks": ["compute", "networks"],
                "resource_policies": ["compute", "resource-policies"], "buckets": ["storage", "buckets"],
                "logging_sinks": ["logging", "sinks"]}
    commands = {key: ["gcloud", *parts, "list", project, "--format=json"] for key, parts in families.items()}
    commands["scheduler_jobs"] = ["gcloud", "scheduler", "jobs", "list", project, "--location=" + c["region"], "--format=json"]
    commands["logging_buckets"] = ["gcloud", "logging", "buckets", "list", project, "--location=global", "--format=json"]
    commands["enabled_services"] = ["gcloud", "services", "list", "--enabled", project, "--format=json"]
    commands["billing"] = ["gcloud", "billing", "projects", "describe", c["project"], project, "--format=json"]
    return commands


def deletion_commands(c, observations, verified_object_uris=None):
    """Explicit project-scoped deletions, ordered before billing disconnection.

    Failed inventories stay unverified. Project shutdown is still needed for
    services outside this workload and recoverably retained provider data.
    """
    project = "--project=" + c["project"]
    families = {"instances": ["compute", "instances"], "forwarding_rules": ["compute", "forwarding-rules"],
                "routers": ["compute", "routers"], "addresses": ["compute", "addresses"],
                "disks": ["compute", "disks"], "snapshots": ["compute", "snapshots"],
                "images": ["compute", "images"], "resource_policies": ["compute", "resource-policies"],
                "firewalls": ["compute", "firewall-rules"], "subnets": ["compute", "networks", "subnets"],
                "networks": ["compute", "networks"]}
    commands = []
    for kind, parts in families.items():
        for item in observations[kind].get("result") or []:
            link = item.get("selfLink", "")
            if "/projects/" + c["project"] + "/" not in link:
                raise GuardError("resource selfLink lies outside the dedicated project")
            command = ["gcloud", *parts, "delete", item["name"], project, "--quiet"]
            if item.get("zone"): command.append("--zone=" + item["zone"].split("/")[-1])
            elif item.get("region"): command.append("--region=" + item["region"].split("/")[-1])
            elif kind in ("addresses", "forwarding_rules"): command.append("--global")
            commands.append(command)
    for item in observations["scheduler_jobs"].get("result") or []:
        if not item["name"].startswith("projects/" + c["project"] + "/"):
            raise GuardError("schedule lies outside the dedicated project")
        commands.append(["gcloud", "scheduler", "jobs", "delete", item["name"], project, "--quiet"])
    for item in observations["logging_sinks"].get("result") or []:
        commands.append(["gcloud", "logging", "sinks", "delete", item["name"], project, "--quiet"])
    for item in observations["buckets"].get("result") or []:
        name = item.get("name", item.get("id")).removeprefix("gs://").rstrip("/")
        if verified_object_uris is None:raise GuardError('bucket deletion requires the verified archived generation roster')
        # Existing soft-deleted generations retain their original policy; they
        # cannot be silently asserted purged by changing the current policy.
        commands.append(["gcloud", "storage", "buckets", "update", "gs://" + name, "--soft-delete-duration=0", project, "--quiet"])
        for uri in verified_object_uris:
            if uri.startswith('gs://'+name+'/'):
                commands.append(["gcloud", "storage", "rm", uri, project, "--quiet"])
        # Never recursively delete an object that appeared after verification.
        # An unexpected generation leaves the bucket nonempty and visible.
        commands.append(["gcloud", "storage", "rm", "gs://" + name, project, "--quiet"])
    return commands


def closeout(c, folder, receipt, execute=False):
    if c.get("dedicated_project") is not True or c.get("paid_actions_authorized") is not True or type(execute) is not bool:
        raise GuardError("closeout requires explicit dedicated-project authorization")
    folder = Path(folder)
    with locked(folder / "coordinator.lock"):
        already_closing=(folder/'closeout-started.json').exists()
        verified = verify_archives(receipt,c)
        g = Gcloud(c, folder / "commands")
        owner = g.run(["gcloud", "projects", "describe", c["project"], "--project=" + c["project"], "--format=json"])
        if owner["exit"] != 0:
            raise GuardError("cannot verify project identity/ownership")
        p = json.loads(owner["stdout"])
        if p.get("projectId") != c["project"] or p.get("labels", {}).get("dams-task") != digest(c["authorization_id"])[:24]:
            raise GuardError("project lacks the matching dedicated task label")
        if execute:
            atomic(folder/'closeout-started.json',{'utc':stamp(),'authorization_sha256':digest(c['authorization_id']),
                                                  'closure_mode':verified['closure_mode'],'new_execution_forbidden':True})
        observations = {}
        for kind, command in inventory_commands(c).items():
            r = g.run(command)
            observations[kind] = {"exit": r["exit"], "result": json.loads(r["stdout"]) if r["exit"] == 0 else None,
                                  "status": "observed" if r["exit"] == 0 else "unverified"}
        # Explicit storage versions, soft-deletion and retention policies.
        for bucket in observations["buckets"].get("result") or []:
            name = bucket.get("name", bucket.get("id"))
            if not name:
                raise GuardError("bucket name unavailable")
            uri = "gs://" + name.removeprefix("gs://")
            observations["bucket:" + name] = {}
            for key, command in {
                "policy": ["gcloud", "storage", "buckets", "describe", uri, "--project=" + c["project"], "--format=json"],
                "versions": ["gcloud", "storage", "ls", "--all-versions", "--recursive", uri, "--project=" + c["project"], "--format=json"],
                "soft_deleted": ["gcloud", "storage", "ls", "--soft-deleted", "--recursive", uri, "--project=" + c["project"], "--format=json"]}.items():
                r = g.run(command)
                observations["bucket:" + name][key] = {"exit": r["exit"], "stdout": r["stdout"], "stderr": r["stderr"]}
        atomic(folder / "pre-closeout-inventory.json", {"utc": stamp(), "archives": verified, "resources": observations})
        for kind in ('instances','disks'):
            if observations[kind]['exit']!=0 or observations[kind]['result']:
                raise GuardError('final closeout requires confirmed VM and disk absence; quiesce writers and preserve their last outputs before re-archiving')
        if observations['buckets']['exit']!=0:raise GuardError('cleanup cannot verify the actual retained bucket roster')
        actual=verify_retained_cloud_objects(c,folder,receipt,observations['buckets']['result'],allow_deleted_archived=already_closing)
        atomic(folder/'retained-object-verification-after-quiescence.json',actual)
        # The final project shutdown covers services outside this narrow CPU
        # workflow. Failed inventory remains unverified, never empty/pass.
        resource_commands = deletion_commands(c, observations,actual['verified_object_uris'])
        shutdown_commands = [
            ["gcloud", "billing", "projects", "unlink", c["project"], "--project=" + c["project"], "--quiet"],
            ["gcloud", "projects", "delete", c["project"], "--project=" + c["project"], "--quiet"]]
        atomic(folder / "closeout-plan.json", {"mode": "execute" if execute else "read-only-plan", "archives": verified,
                                               "resource_commands": resource_commands, "shutdown_commands": shutdown_commands,
                                               "shared_billing_account_untouched": True})
        if not execute:
            return {"status": "planned-only", "archives": verified}
        outcomes=[]
        for command in resource_commands:
            r=g.run(command)
            outcomes.append({"command":command,"exit":r["exit"],"status":"response-confirmed" if r["exit"]==0 else "unconfirmed"})
        atomic(folder / "resource-deletion-responses.json", outcomes)
        post={}
        for kind, command in inventory_commands(c).items():
            r=g.run(command)
            post[kind]={"exit":r["exit"],"result":json.loads(r["stdout"]) if r["exit"]==0 else None,
                        "status":"observed" if r["exit"]==0 else "unverified"}
        atomic(folder / "post-deletion-inventory.json", post)
        for kind in ('instances','disks','buckets'):
            if post[kind]['exit']!=0:raise GuardError('post-deletion writer/storage inventory is unverified')
        if post['instances']['result'] or post['disks']['result']:raise GuardError('writer or disk reappeared during closeout')
        remaining=verify_retained_cloud_objects(c,folder,receipt,post['buckets']['result'],allow_deleted_archived=True)
        atomic(folder/'retained-object-verification-before-project-shutdown.json',remaining)
        if post['buckets']['result']:
            raise GuardError('bucket deletion remains unconfirmed; no project shutdown until uploads cannot commit to a retained bucket')
        # A deleted bucket seals even an already in-flight resumable upload.
        # Reconnect and verify absence after unknown deletion responses; do not
        # sacrifice a late checkpoint merely to report project shutdown.
        for command in shutdown_commands:
            r = g.run(command)
            if r["exit"] != 0:
                raise GuardError("project billing/shutdown response unconfirmed; retain open closeout status")
        billing = g.run(["gcloud", "billing", "projects", "describe", c["project"], "--project=" + c["project"], "--format=json"])
        project = g.run(["gcloud", "projects", "describe", c["project"], "--project=" + c["project"], "--format=json"])
        if billing["exit"] != 0 or project["exit"] != 0:
            raise GuardError("post-closeout identity/billing state cannot be verified")
        bd, pd = json.loads(billing["stdout"]), json.loads(project["stdout"])
        if bd.get("billingEnabled") is not False or bd.get("billingAccountName") not in (None, "") or pd.get("lifecycleState") != "DELETE_REQUESTED":
            raise GuardError("project still billing-linked or not shutdown")
        result = {"status": "billing-disabled-project-shutdown-verified", "utc": stamp(), "archives": verified,
                  'science_complete':verified['science_complete'],'closure_mode':verified['closure_mode'],
                  "billing_enabled": False, "project_state": "DELETE_REQUESTED", "shared_billing_account_untouched": True,
                  "resource_deletion_response_count":len(outcomes),
                  "unconfirmed_resource_deletions":sum(x['exit']!=0 for x in outcomes),
                  "limitations": "Delete-requested is the provider's recoverable shutdown state, not immediate physical erasure. Prior accrued charges can settle later. Retained resource inventory failures stay unverified."}
        atomic(folder / "closeout-verification.json", result)
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--private-config", type=Path, required=True)
    p.add_argument("--archive-receipt", type=Path, required=True)
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    a = p.parse_args()
    if a.private_config.stat().st_mode & 0o077:
        raise GuardError("private config requires mode 0600")
    result = closeout(json.loads(a.private_config.read_text()), a.state_dir, json.loads(a.archive_receipt.read_text()), a.execute)
    print(json.dumps(result, indent=2))


if __name__ == "__main__": main()
