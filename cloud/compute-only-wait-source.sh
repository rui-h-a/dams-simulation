#!/bin/bash
# No credentials/download/science; root uploads an approved package via IAP next.
set -euo pipefail
umask 077
install -d -m 700 /opt/dams /var/lib/dams-compute

# OS Login remains enabled. Publish only this VM's actual SSH PUBLIC keys;
# the coordinator still enrolls them through its authenticated provider readback.
/usr/bin/python3 -I -B <<'DAMS_PUBLIC_HOSTKEY_PY'
import base64
from contextlib import ExitStack
import hashlib
import http.client
import os
from pathlib import Path
import signal
import stat
import subprocess
import time

KEYS = (
    ('ssh-rsa', '/etc/ssh/ssh_host_rsa_key.pub'),
    ('ssh-ed25519', '/etc/ssh/ssh_host_ed25519_key.pub'),
    ('ecdsa-sha2-nistp256', '/etc/ssh/ssh_host_ecdsa_key.pub'),
)
HOST = '169.254.169.254'
PREFIX = '/computeMetadata/v1/instance/guest-attributes/hostkeys/'
MAX_PUBLIC_BYTES = 16384

def require(ok):
    if not ok:
        raise ValueError('public hostkey publishing refused')

def identity(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid,
            s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

def wire_public(raw, kind):
    require(kind in {item[0] for item in KEYS} and isinstance(raw, bytes)
            and 0 < len(raw) <= MAX_PUBLIC_BYTES)
    line = raw[:-1] if raw.endswith(b'\n') else raw
    require(b'\n' not in line and b'\r' not in line)
    fields = line.split()
    require(len(fields) >= 2 and fields[0] == kind.encode('ascii'))
    value = fields[1]
    require(len(value) <= MAX_PUBLIC_BYTES)
    decoded = base64.b64decode(value, validate=True)
    require(base64.b64encode(decoded) == value)
    offset = 0
    def string():
        nonlocal offset
        require(offset + 4 <= len(decoded))
        length = int.from_bytes(decoded[offset:offset+4], 'big')
        offset += 4
        require(length > 0 and offset + length <= len(decoded))
        result = decoded[offset:offset+length]; offset += length
        return result
    require(string() == kind.encode('ascii'))
    if kind == 'ssh-ed25519':
        require(len(string()) == 32)
    elif kind == 'ecdsa-sha2-nistp256':
        require(string() == b'nistp256')
        point = string(); require(len(point) == 65 and point[0] == 4)
    else:
        for _ in range(2):
            integer = string()
            require(not integer[0] & 128 and any(integer)
                    and (len(integer) == 1 or integer[0] != 0 or integer[1] & 128))
    require(offset == len(decoded))
    return value

def read_public(path, kind, *, expected_owner=0):
    path = Path(path).absolute()
    with ExitStack() as stack:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        anchors = []
        for part in path.parent.parts[1:]:
            parent = fd
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, expected_owner)
                    and not info.st_mode & 0o022)
            anchors.append((parent, part, fd, info.st_dev, info.st_ino))
        def check():
            for parent, name, held, device, inode in anchors:
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                actual = os.fstat(held)
                require(stat.S_ISDIR(current.st_mode) and stat.S_ISDIR(actual.st_mode)
                        and (current.st_dev, current.st_ino) == (device, inode)
                        and (actual.st_dev, actual.st_ino) == (device, inode)
                        and current.st_uid in (0, expected_owner)
                        and not current.st_mode & 0o022)
        check()
        before = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == expected_owner
                and before.st_nlink == 1 and not before.st_mode & 0o022
                and 0 < before.st_size <= MAX_PUBLIC_BYTES)
        key_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        stack.callback(os.close, key_fd)
        require(identity(os.fstat(key_fd)) == identity(before))
        with os.fdopen(key_fd, 'rb', closefd=False) as stream:
            raw = stream.read(MAX_PUBLIC_BYTES+1)
            require(len(raw) <= MAX_PUBLIC_BYTES and stream.read(1) == b'')
        check()
        require(identity(os.fstat(key_fd)) == identity(before)
                and identity(os.stat(path.name, dir_fd=fd, follow_symlinks=False)) == identity(before))
        value = wire_public(raw, kind)
        return {'value': value, 'identity': identity(before), 'sha256': hashlib.sha256(raw).hexdigest()}

def request(kind, method, value, *, connection_factory=http.client.HTTPConnection):
    require(kind in {item[0] for item in KEYS} and method in ('PUT', 'GET')
            and isinstance(value, bytes) and 0 < len(value) <= MAX_PUBLIC_BYTES)
    connection = connection_factory(HOST, timeout=5)
    response = None
    try:
        connection.request(method, PREFIX+kind, body=value if method == 'PUT' else None,
                           headers={'Metadata-Flavor': 'Google', 'Content-Type': 'text/plain'})
        response = connection.getresponse()
        require(response.status == 200 and response.getheader('Metadata-Flavor') == 'Google')
        bound = 1024 if method == 'PUT' else len(value)
        raw = response.read(bound+1)
        require(len(raw) <= bound and response.read(1) == b'')
        if method == 'GET':
            require(raw == value)
    finally:
        if response is not None:
            response.close()
        connection.close()

def publish(keys, check, *, connection_factory=http.client.HTTPConnection, sleep=time.sleep):
    require(set(keys) == {item[0] for item in KEYS})
    for kind, _ in KEYS:
        for method in ('PUT', 'GET'):
            check()
            request(kind, method, keys[kind]['value'], connection_factory=connection_factory)
            check()
            # Six finite operations remain below the documented 3/s and 10/min.
            sleep(0.35)

def main():
    require(os.geteuid() == 0)
    def alarm(*_):
        raise TimeoutError('bounded public hostkey startup')
    signal.signal(signal.SIGALRM, alarm)
    signal.alarm(90)
    try:
        keys = {}; missing = False
        for kind, path in KEYS:
            try:
                keys[kind] = read_public(path, kind)
            except FileNotFoundError:
                missing = True
        if missing:
            subprocess.run(['/usr/bin/ssh-keygen', '-A'], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=20, check=True)
            keys = {kind: read_public(path, kind) for kind, path in KEYS}
        def check():
            require(keys == {kind: read_public(path, kind) for kind, path in KEYS})
        publish(keys, check)
    finally:
        signal.alarm(0)

if __name__ == '__main__':
    try:
        main()
    except BaseException:
        raise SystemExit('DAMS_PUBLIC_HOSTKEY_PUBLISH_REFUSED') from None
DAMS_PUBLIC_HOSTKEY_PY
