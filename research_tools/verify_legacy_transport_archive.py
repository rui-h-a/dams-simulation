"""Read-only, bounded verifier for the legacy exact-byte case transport TAR.

Only the transport is adapted: the existing public 4 MiB codec validates and
decodes every logical reference. No extraction, raw cache, Model, lifecycle
registration, retirement authority, provider access or new world is involved.
The explicit outer SHA/length must come from the retained transport receipt.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import resource
import stat
import sys
import tarfile
import time

import cloud_archive as codec
from cloud_control import GuardError

BLOCK = 64 * 1024
MAX_TAR_BYTES = 2 * 1024**3 - 1
MAX_METADATA_BYTES = 2 * 1024**2
MAX_TOTAL_METADATA_BYTES = 8 * 1024**2
MAX_FILES = 13
MAX_REFERENCES = 8192
MAX_UNIQUE_CHUNKS = 4096
MAX_RAW_BYTES = 16 * 1024**3
MAX_SECONDS = 300
MAX_RSS_BYTES = 256 * 1024**2
MAX_PROOF_BYTES = 32 * 1024
INDEX_SCHEMA = "DAMS-private-exact-byte-case-bundle-index-1"
GATE_FILES = {"original-gate-input-file-hashes.json", "original-gate-verified-case.json",
              "process-exit.json", "result.json"}


class Rejected(GuardError):
    """An exact-byte or transport contract was not satisfied."""


class Censored(RuntimeError):
    """A finite resource envelope ended before complete verification."""


def require(condition, reason):
    if not condition:
        raise Rejected(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def anchor(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


class Budget:
    """Sampled process bounds, not OS isolation or a scientific runtime gate."""
    def __init__(self):
        self.start = time.monotonic()
        self.read_bytes = 0
        self.peak_rss = 0

    def check(self, count=0):
        self.read_bytes += count
        if time.monotonic() - self.start >= MAX_SECONDS:
            raise Censored("300-second verification deadline reached")
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform != "darwin":
            rss *= 1024
        self.peak_rss = max(self.peak_rss, rss)
        if rss > MAX_RSS_BYTES:
            raise Censored("256 MiB process RSS envelope exceeded")


def open_regular(path):
    path = codec.lexical_path(path)
    before = anchor(path.lstat())
    require(stat.S_ISREG(before[2]), "input must be a regular file")
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    stream = os.fdopen(fd, "rb")
    if anchor(os.fstat(stream.fileno())) != before:
        stream.close()
        raise Rejected("input identity changed before read")
    return path, stream, before


def unchanged(path, stream, before):
    require(anchor(os.fstat(stream.fileno())) == before and anchor(path.lstat()) == before,
            "input changed during verification")


def read_exact(stream, size, budget):
    raw = stream.read(size)
    budget.check(len(raw))
    require(len(raw) == size, "truncated transport member or header")
    return raw


def read_metadata(path, budget):
    path, stream, before = open_regular(path)
    with stream:
        require(before[4] <= MAX_METADATA_BYTES, "metadata exceeds 2 MiB bound")
        raw = read_exact(stream, before[4], budget)
        require(stream.read(1) == b"", "metadata has trailing bytes")
        unchanged(path, stream, before)
    return raw, (path, before)


def parse_json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, "duplicate JSON key")
            value[key] = item
        return value
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(Rejected("nonfinite JSON")))
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        if isinstance(error, Rejected):
            raise
        raise Rejected("invalid metadata JSON") from error


def file_name(name):
    codec.safe_name(name)
    require("/" not in name, "legacy raw file name must be a basename")
    return name


def plain_guard_definition(raw):
    """Require the literal plain declaration; this is not module isolation."""
    try:
        tree = ast.parse(raw)
    except (SyntaxError, ValueError, RecursionError) as error:
        raise Rejected("invalid exception dependency source") from error
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "GuardError"]
    require(len(classes) == 1, "exception dependency class definition differs")
    node = classes[0]
    require(len(node.bases) == 1 and isinstance(node.bases[0], ast.Name)
            and node.bases[0].id == "RuntimeError" and not node.keywords and not node.decorator_list
            and len(node.body) == 1 and isinstance(node.body[0], ast.Pass),
            "exception dependency class has behavior")


def sole_guard_import(raw):
    tree = ast.parse(raw)
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
               and ((isinstance(node, ast.ImportFrom) and node.module == "cloud_control")
                    or (isinstance(node, ast.Import) and any(n.name == "cloud_control" for n in node.names)))]
    require(len(imports) == 1 and isinstance(imports[0], ast.ImportFrom)
            and imports[0].level == 0 and len(imports[0].names) == 1
            and imports[0].names[0].name == "GuardError" and imports[0].names[0].asname is None,
            "public codec control dependency is not solely GuardError")


def load_metadata(index_path, descriptor_root, budget):
    index_raw, index_anchor = read_metadata(index_path, budget)
    index = parse_json(index_raw)
    require(isinstance(index, dict) and index.get("schema") == INDEX_SCHEMA
            and index.get("codec") == codec.CODEC
            and index.get("chunk_fetch_rule") == "encoded/<encoded_sha256>",
            "unsupported legacy archive index")
    codec.sha256(index.get("case_id"))
    files = index.get("files")
    require(isinstance(files, list) and len(files) == MAX_FILES,
            "complete 13-file index required")
    bindings = index.get("original_file_bindings")
    require(isinstance(bindings, list) and len(bindings) == MAX_FILES,
            "complete original file bindings required")
    bound = {}
    for item in bindings:
        name = file_name(item["file"])
        require(name not in bound, "duplicate original file binding")
        bound[name] = (codec.natural(item["bytes"], "binding bytes"),
                       codec.sha256(item["raw_sha256"]))
    root = codec.lexical_path(descriptor_root)
    require(stat.S_ISDIR(root.lstat().st_mode), "descriptor root must be a directory")
    root_anchor = anchor(root.lstat())
    metadata = {"archive-index.json": index_raw}
    anchors = [index_anchor]
    descriptors = []
    objects = {}
    occurrences = Counter()
    names = set()
    total_metadata = len(index_raw)
    raw_sum = refs = 0
    for item in files:
        name = file_name(item["file"])
        descriptor_name = "descriptors/" + name + ".json"
        require(name not in names and item["descriptor"] == descriptor_name,
                "duplicate file or unexpected descriptor path")
        names.add(name)
        raw, retained = read_metadata(root / (name + ".json"), budget)
        total_metadata += len(raw)
        require(total_metadata <= MAX_TOTAL_METADATA_BYTES, "metadata total exceeds 8 MiB bound")
        require(sha(raw) == codec.sha256(item["descriptor_sha256"]), "descriptor exact-byte SHA differs")
        descriptor = codec.validate_manifest(parse_json(raw))
        codec.natural(item["bytes"], "index raw bytes")
        codec.sha256(item["raw_sha256"])
        codec.natural(item["encoded_bytes"], "index encoded bytes")
        codec.natural(item["chunk_count"], "index chunk count")
        require((descriptor["raw_bytes"], descriptor["raw_sha256"]) ==
                (item["bytes"], item["raw_sha256"]) == bound.get(name),
                "descriptor differs from original full-file pins")
        require(descriptor["encoded_bytes"] == item["encoded_bytes"]
                and len(descriptor["chunks"]) == item["chunk_count"],
                "descriptor totals differ from index")
        raw_sum += descriptor["raw_bytes"]
        refs += len(descriptor["chunks"])
        require(raw_sum <= MAX_RAW_BYTES and refs <= MAX_REFERENCES,
                "raw bytes or reference count exceeds verification envelope")
        for chunk in descriptor["chunks"]:
            key = chunk["encoded_sha256"]
            shape = {field: chunk[field] for field in ("encoded_bytes", "raw_bytes", "raw_sha256")}
            require(key not in objects or objects[key] == shape, "inconsistent shared encoded chunk")
            objects[key] = shape
            occurrences[key] += 1
            require(len(objects) <= MAX_UNIQUE_CHUNKS, "unique chunk count exceeds envelope")
        metadata[descriptor_name] = raw
        anchors.append(retained)
        descriptors.append((name, descriptor))
    require(names == set(bound), "original binding roster differs")
    expected_descriptors = {name + ".json" for name in names}
    seen_descriptors = set()
    # Path.iterdir() can first materialize all directory entries (Python 3.14).
    # Direct scandir preserves the entry-count/RSS/deadline bound before refusal.
    with os.scandir(root) as entries:
        for entry in entries:
            budget.check()
            require(len(seen_descriptors) < MAX_FILES and entry.name in expected_descriptors
                    and entry.name not in seen_descriptors, "external descriptor roster differs")
            seen_descriptors.add(entry.name)
    require(seen_descriptors == expected_descriptors, "external descriptor roster differs")
    require(raw_sum == index.get("original_all_logical_bytes")
            and len(objects) == index.get("unique_encoded_chunks")
            and sum(item["encoded_bytes"] for item in objects.values()) == index.get("unique_encoded_bytes"),
            "index archive totals differ")
    for field in ("original_all_logical_bytes", "unique_encoded_chunks", "unique_encoded_bytes"):
        codec.natural(index[field], "index total")
    source_pins = index.get("codec_source_pins")
    gate_pins = index.get("original_gate_pins")
    require(isinstance(source_pins, dict) and set(source_pins) == {"cloud_archive.py", "cloud_control.py"}
            and isinstance(gate_pins, dict) and set(gate_pins) == GATE_FILES,
            "legacy source/evidence roster differs")
    pins = {}
    current_source_pins = {}
    require(codec.GuardError is GuardError and GuardError.__bases__ == (RuntimeError,)
            and type(GuardError) is type and GuardError.__init__ is RuntimeError.__init__
            and GuardError.__new__ is RuntimeError.__new__
            and GuardError.__str__ is RuntimeError.__str__
            and not (set(GuardError.__dict__) - {"__module__", "__doc__", "__weakref__",
                                               "__firstlineno__", "__static_attributes__"}),
            "unexpected public codec exception dependency")
    for name, expected in source_pins.items():
        raw, retained = read_metadata(Path(codec.__file__).absolute().parent / name, budget)
        expected = codec.sha256(expected)
        current_source_pins[name] = sha(raw)
        # cloud_control is imported solely for its unchanged GuardError class.
        # Its unrelated current control code need not be the historical source.
        if name == "cloud_archive.py":
            require(sha(raw) == expected, "public codec source drift")
            sole_guard_import(raw)
        else:
            require(Path(sys.modules[GuardError.__module__].__file__).absolute() == retained[0],
                    "public exception dependency imported from another source")
            plain_guard_definition(raw)
        pins["source/" + name] = expected
        anchors.append(retained)
    for name, expected in gate_pins.items():
        pins["evidence/" + name] = codec.sha256(expected)
    expected_names = (set(metadata) | set(pins) | {"encoded/" + key for key in objects}
                      | {"encoded-chunk-catalog.json", "evidence/admission.json"})
    return (index, metadata, anchors, root, root_anchor, descriptors, objects,
            occurrences, pins, expected_names, total_metadata, current_source_pins)


def strict_header(raw):
    """Legacy TarInfo headers have no PAX/GNU extensions or lexical path tails."""
    require(raw[257:263] == b"ustar\0" and raw[263:265] == b"00", "unsupported TAR header format")
    for field in (raw[:100], raw[157:257], raw[345:500]):
        if b"\0" in field:
            require(not any(field[field.index(b"\0") + 1:]), "nonzero TAR path tail")
    try:
        member = tarfile.TarInfo.frombuf(raw, "ascii", "strict")
    except (tarfile.HeaderError, UnicodeError, ValueError) as error:
        raise Rejected("invalid TAR header or checksum") from error
    require(member.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and not member.linkname,
            "TAR member must be regular, without links or extensions")
    codec.safe_name(member.name)
    require(member.mode == 0o600 and member.uid == member.gid == member.mtime == 0
            and member.uname == member.gname == "" and member.devmajor == member.devminor == 0,
            "legacy TAR header metadata differs")
    return member


def scan_tar(stream, expected_bytes, metadata, objects, pins, expected_names, budget, initial_metadata_bytes):
    members = {}
    metadata_bytes = initial_metadata_bytes
    extra_metadata = {}
    while True:
        header_offset = stream.tell()
        require(header_offset + 512 <= expected_bytes, "TAR is missing normal EOF")
        header = read_exact(stream, 512, budget)
        if header == bytes(512):
            require(read_exact(stream, 512, budget) == bytes(512), "TAR requires two zero EOF blocks")
            while stream.tell() < expected_bytes:
                require(not any(read_exact(stream, min(BLOCK, expected_bytes - stream.tell()), budget)),
                        "nonzero bytes after TAR EOF")
            require(stream.read(1) == b"", "physical tail after expected TAR length")
            break
        member = strict_header(header)
        name, size, offset = member.name, member.size, stream.tell()
        require(name in expected_names and name not in members, "unexpected or duplicate TAR member")
        require(type(size) is int and size >= 0, "invalid TAR member length")
        end = offset + size
        padded_end = end + (-size % 512)
        require(offset == header_offset + 512 and padded_end <= expected_bytes - 1024,
                "TAR member offset or length exceeds transport bounds")
        key = name.removeprefix("encoded/") if name.startswith("encoded/") else None
        if key is not None:
            require(size == objects[key]["encoded_bytes"] <= codec.MAX_ENCODED_CHUNK,
                    "encoded TAR member length differs")
        else:
            require(size <= MAX_METADATA_BYTES, "TAR metadata member exceeds 2 MiB bound")
            if name not in metadata:
                metadata_bytes += size
                require(metadata_bytes <= MAX_TOTAL_METADATA_BYTES, "TAR metadata total exceeds 8 MiB bound")
        digest = hashlib.sha256()
        chunks = [] if key is None else None
        remaining = size
        while remaining:
            block = read_exact(stream, min(BLOCK, remaining), budget)
            digest.update(block)
            if chunks is not None:
                chunks.append(block)
            remaining -= len(block)
        member_sha = digest.hexdigest()
        if key is not None:
            require(member_sha == key, "encoded TAR member SHA differs")
        else:
            raw = b"".join(chunks)
            if name in metadata:
                require(raw == metadata[name] and member_sha == sha(metadata[name]),
                        "TAR index/descriptor exact bytes differ")
            if name in pins:
                require(member_sha == pins[name], "TAR source/evidence member SHA differs")
            if name == "source/cloud_control.py":
                plain_guard_definition(raw)
            if name in {"encoded-chunk-catalog.json", "evidence/admission.json"}:
                extra_metadata[name] = raw
        require(not any(read_exact(stream, -size % 512, budget)), "nonzero TAR member padding")
        members[name] = (offset, size, member_sha)
        require(len(members) <= MAX_UNIQUE_CHUNKS + MAX_FILES + 9, "TAR member count exceeds envelope")
    require(set(members) == expected_names, "incomplete TAR member roster")
    return members, extra_metadata


def verify(tar_path, index_path, descriptor_root, expected_tar_sha256, expected_tar_bytes):
    """Return a small proof only after all 13 complete raw hashes pass."""
    budget = Budget()
    codec.sha256(expected_tar_sha256)
    require(type(expected_tar_bytes) is int and 10240 <= expected_tar_bytes <= MAX_TAR_BYTES
            and expected_tar_bytes % 10240 == 0, "expected TAR length exceeds or differs from legacy envelope")
    (index, metadata, anchors, root, root_anchor, descriptors, objects, occurrences,
     pins, expected_names, metadata_bytes, current_source_pins) = load_metadata(index_path, descriptor_root, budget)
    tar_path, stream, before = open_regular(tar_path)
    with stream:
        require(before[4] == expected_tar_bytes, "TAR stat length differs from external pin")
        complete = hashlib.sha256()
        count = 0
        while block := stream.read(BLOCK):
            budget.check(len(block))
            count += len(block)
            require(count <= expected_tar_bytes, "TAR grew beyond external length")
            complete.update(block)
        require(count == expected_tar_bytes and complete.hexdigest() == expected_tar_sha256,
                "complete TAR SHA/length differs from external pin")
        unchanged(tar_path, stream, before)
        stream.seek(0)
        members, extra = scan_tar(stream, expected_tar_bytes, metadata, objects, pins, expected_names,
                                  budget, metadata_bytes)
        catalog = parse_json(extra["encoded-chunk-catalog.json"])
        require(isinstance(catalog, dict) and set(catalog) == set(objects),
                "encoded catalog roster differs from descriptors")
        for key, item in catalog.items():
            budget.check()
            require(isinstance(item, dict) and set(item) == {"shape", "logical_occurrences"}
                    and isinstance(item["shape"], dict)
                    and set(item["shape"]) == {"encoded_bytes", "raw_bytes", "raw_sha256"},
                    "encoded catalog shape fields differ")
            codec.natural(item["logical_occurrences"], "catalog occurrences", positive=True)
            codec.natural(item["shape"]["encoded_bytes"], "catalog encoded bytes", positive=True)
            codec.natural(item["shape"]["raw_bytes"], "catalog raw bytes", positive=True)
            codec.sha256(item["shape"]["raw_sha256"])
        require(catalog == {key: {"shape": shape, "logical_occurrences": occurrences[key]}
                            for key, shape in objects.items()}, "encoded catalog differs from descriptors")
        admission = parse_json(extra["evidence/admission.json"])
        require(isinstance(admission, dict) and admission.get("case_id") == index["case_id"],
                "legacy admission case differs")
        verified_files = []
        refs = raw_sum = 0
        for name, descriptor in descriptors:
            full = hashlib.sha256()
            offset = encoded_sum = 0
            for chunk in descriptor["chunks"]:
                budget.check()
                require(chunk["offset"] == offset, "raw chunk order, gap or overlap differs")
                member_offset, member_size, _ = members["encoded/" + chunk["encoded_sha256"]]
                stream.seek(member_offset)
                encoded = read_exact(stream, member_size, budget)
                raw = codec.decode_chunk(encoded, chunk)
                full.update(raw)
                offset += len(raw)
                encoded_sum += len(encoded)
                refs += 1
                del encoded, raw
            require(offset == descriptor["raw_bytes"] and full.hexdigest() == descriptor["raw_sha256"]
                    and encoded_sum == descriptor["encoded_bytes"], "complete raw file SHA/length differs")
            raw_sum += offset
            verified_files.append({"file": name, "raw_bytes": offset, "raw_sha256": full.hexdigest(),
                                   "chunk_references": len(descriptor["chunks"]), "status": "PASS"})
        unchanged(tar_path, stream, before)
    for path, retained in anchors:
        require(anchor(path.lstat()) == retained, "metadata or codec source changed before sealing")
    require(anchor(root.lstat()) == root_anchor, "descriptor root changed before sealing")
    budget.check()
    return {"schema": "DAMS-legacy-transport-binary-proof-1", "status": "PASS",
            "scope": "Transport and complete raw byte hashes only; no Model or scientific restore, lifecycle admission, retirement authority or new world.",
            "case_id": index["case_id"], "tar_bytes": count, "tar_sha256": complete.hexdigest(),
            "normal_tar_eof": True, "tar_member_count": len(members), "index_sha256": sha(metadata["archive-index.json"]),
            "complete_raw_verification": True, "all13_files_full_raw_sha256": True,
            "all_encoded_members_full_sha256": True,
            "descriptor_bytes": sum(len(raw) for name, raw in metadata.items() if name.startswith("descriptors/")),
            "decoder_source_matches_legacy": True,
            "archived_codec_source_sha256": index["codec_source_pins"],
            "current_codec_source_sha256": current_source_pins,
            "cloud_control_dependency_is_exception_class_only": True,
            "raw_files": len(verified_files), "raw_bytes": raw_sum, "chunk_references": refs,
            "unique_encoded_chunks": len(objects), "unique_encoded_bytes": sum(s["encoded_bytes"] for s in objects.values()),
            "files": verified_files, "model_constructions": 0, "new_scientific_samples": 0,
            "formal_worlds_added": 0, "materialized_raw_bytes": 0, "extracted_encoded_bytes": 0,
            "network_calls": 0, "lifecycle_admissions": 0, "retirement_authority": False,
            "limits": {"tar_bytes": MAX_TAR_BYTES, "metadata_member_bytes": MAX_METADATA_BYTES,
                       "metadata_total_bytes": MAX_TOTAL_METADATA_BYTES, "raw_bytes": MAX_RAW_BYTES,
                       "raw_chunk_bytes": codec.CHUNK_BYTES, "encoded_chunk_bytes": codec.MAX_ENCODED_CHUNK,
                       "raw_files": MAX_FILES, "references": MAX_REFERENCES, "unique_chunks": MAX_UNIQUE_CHUNKS,
                       "wall_seconds": MAX_SECONDS, "rss_bytes": MAX_RSS_BYTES, "proof_bytes": MAX_PROOF_BYTES},
            "wall_seconds": time.monotonic() - budget.start, "peak_rss_bytes": budget.peak_rss,
            "input_read_bytes": budget.read_bytes, "sampled_limits_are_not_os_isolation": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tar", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--descriptor-root", type=Path, required=True)
    parser.add_argument("--expected-tar-sha256", required=True)
    parser.add_argument("--expected-tar-bytes", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        result = verify(args.tar, args.index, args.descriptor_root,
                        args.expected_tar_sha256, args.expected_tar_bytes)
    except (GuardError, Censored, OSError, KeyError, TypeError, ValueError) as error:
        # Keep paths, private metadata, endpoints and traceback out of the proof.
        result = {"schema": "DAMS-legacy-transport-binary-proof-1",
                  "status": "CENSORED" if isinstance(error, Censored) else "FAILED",
                  "error_type": type(error).__name__, "complete_raw_verification": False,
                  "reason": str(error) if isinstance(error, (Rejected, Censored)) else "input or codec contract failed",
                  "model_constructions": 0, "formal_worlds_added": 0,
                  "materialized_raw_bytes": 0, "retirement_authority": False}
    raw = json.dumps(result, sort_keys=True, allow_nan=False)
    require(len(raw.encode()) + 1 <= MAX_PROOF_BYTES, "proof exceeds 32 KiB bound")
    print(raw)
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
