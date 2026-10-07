# Optional CPU container packaging

This recipe provides inspectable packaging for the existing Python scientific driver. The measured and executed research route remains native macOS on the host recorded in the [support guide](REPRODUCIBILITY.md). Container builds, Linux execution, multi-architecture numerical equivalence and container hardware timing are **unverified**. The local attempts below could not reach a Docker daemon; no application image was built, run or published.

## Fixed upstream identities

The Dockerfile pins both the readable version tag and the immutable OCI image-index digest. Anonymous registry API responses were retrieved on **2026-10-07 15:34 UTC** (2026-10-08 00:34 JST). For each response, the SHA-256 of its original response bytes exactly matched its `Docker-Content-Digest` header. The indices had media type `application/vnd.oci.image.index.v1+json`.

| Component | Fixed reference | Index bytes |
|---|---|---:|
| CPython / Debian slim | `python:3.14.2-slim@sha256:1a3c6dbfd2173971abba880c3cc2ec4643690901f6ad6742d0827bae6cefc925` | 10,365 |
| Official Astral uv binary | `ghcr.io/astral-sh/uv:0.9.26@sha256:9a23023be68b2ed09750ae636228e903a54a05ea56ed03a934d00fe9fbeded4b` | 2,195 |

The Python index identifies Debian `trixie-slim` and Python `3.14.2-slim-trixie`. Its runnable entries are `linux/amd64`, `linux/arm/v5`, `linux/arm/v7`, `linux/arm64/v8`, `linux/386`, `linux/ppc64le`, `linux/riscv64` and `linux/s390x`. The uv index offers runnable entries only for `linux/amd64` and `linux/arm64`. Accordingly, this recipe specifies **linux/amd64 and linux/arm64 as build targets**, without certifying either. Entries with `unknown/unknown` platform metadata are attestations, not extra runnable architectures.

| Target | Python platform manifest | uv platform manifest |
|---|---|---|
| `linux/amd64` | `sha256:51f5baff157fee39a31e5b32394dde7ed2977bcea7a0b16a8978a8d23c270f85` | `sha256:08a7428e3daeb4ff634fe06d3d9aec278579e88f770b5d141e5a408cb998f40a` |
| `linux/arm64` | `sha256:6e92f7404b4a14aeed4f6c1fdba21b2de0014c62465c7bdf93e6a7e94d58460e` (`v8`) | `sha256:38c3fa4690843b611c40ee67ba2227531589418f897fca18ae96c7b66cfb2526` |

Verification used the [Docker Official Images Python repository](https://github.com/docker-library/python), the official Docker registry endpoint `https://registry-1.docker.io/v2/library/python/manifests/3.14.2-slim`, and the official Astral endpoint `https://ghcr.io/v2/astral-sh/uv/manifests/0.9.26`. Registry authentication tokens were used only in memory and are not stored. The [uv Docker guide](https://docs.astral.sh/uv/guides/integration/docker/) documents copying the official binaries, digest pinning and locked synchronization. [Docker's multi-platform documentation](https://docs.docker.com/build/building/multi-platform/) explains the index/platform distinction.

The build asserts CPython 3.14.2, disables managed Python downloads and runs `uv sync --locked --extra analysis --no-dev --no-cache --python /usr/local/bin/python`. `uv.lock` pins the Python analysis dependencies; it is copied unchanged. This differs from the measured native host's CPython 3.14.6, so exact cross-platform or cross-version numerical identity remains untested. The build needs registry/package access. The installed runtime invokes the environment's Python directly and does not resynchronize dependencies.

These digests identify the upstream images, **not a completed DAMS application image**. Record the application image ID, source commit, Dockerfile and lockfile hashes, build platform and effective run configuration after a successful build. The context deliberately excludes `.git`, so a container run cannot discover the checkout commit through Git. `SOURCE_REVISION` labels are supplied by the builder, not independently verified by the model; its manifests still retain the actual model-source and effective-configuration hashes. Application-image identity and commit linkage must accompany the run as separate execution evidence. Digest pinning constrains upstream bytes; it alone does not certify a bitwise reproducible final build or freeze hardware, kernel and Docker resource limits.

## Build context and scope

`.dockerignore` starts by excluding every path, then admits individually named public files: project metadata and license, the nine model modules, eleven research scripts, four test modules, one reference configuration and selected documentation/assumption registries. The BaKoMa font notice is included because the complete scientific HTML report embeds Computer Modern font data and its license. Both the model and experiment source remain readable under `/app`.

The Dockerfile copies these named directories only after the context allowlist is applied. Raw cases, full state, evidence, generated data, contracts, Git history, local environments, caches, secrets, restricted literature and private thesis files are excluded. New source dependencies require an explicit allowlist update. No host workspace mount is needed; examples mount only a new output directory. The image does not package the separately tested Node/Solidity harness or reproduce native hardware benchmarks automatically.

## Commands on a machine with a working Docker daemon

Run from the public repository root. The following commands are available instructions; they are not successful execution evidence on the current host.

```sh
docker build --platform linux/arm64 --tag dams-research:local .
docker image inspect dams-research:local --format '{{.Id}} {{.Architecture}} {{.Os}}'
docker run --rm --network none dams-research:local python -m dams_sim doctor
```

Use `--platform linux/amd64` on an amd64 host. Native builds are preferable; the recipe does not certify emulator performance. To label a build with an exact checkout, supply `--build-arg SOURCE_REVISION=EXACT_COMMIT` using the commit recorded by `git rev-parse HEAD`. Its default `unrecorded` label deliberately does not invent a source revision.

For persistent smoke output:

```sh
mkdir -p container-output
docker run --rm --network none \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$(pwd)/container-output,target=/work" \
  dams-research:local python -m dams_sim smoke --output /work/smoke
```

The default image user is numeric UID/GID `10001:10001`. The bind-mount example uses the caller's UID/GID so retained output permissions remain usable. Persist study output under `/work`; `/app` retains the built source and environment, while temporary plotting caches use `/tmp`. For the complete fixed scientific study, use a new output path:

```sh
docker run --rm --network none \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$(pwd)/container-output,target=/work" \
  dams-research:local python research_tools/reproduce_thesis.py \
  --output /work/thesis-reproduction --workers 2
```

The driver's free-disk/RAM checks and exact source/configuration/output verification still apply. Its physical-RAM probe uses `os.sysconf`, and its free-disk probe inspects the working directory; these probes do not independently certify a container's cgroup memory limit or a bind mount's free capacity. Docker memory/CPU limits and available output storage must be chosen explicitly for the intended workload. A successful `doctor` or smoke run would not establish completion of this larger study; require its `pipeline_manifest.json` to record `status: complete`. Hardware timing in a container must be newly measured and labeled with its host, platform and resource limits.

## Actual local attempt

On **2026-10-07 15:36 UTC** (2026-10-08 00:36 JST), Docker Engine Community client **29.6.2**, API **1.55**, `darwin/arm64`, default context, attempted each of the following once. No daemon, virtual machine or cloud resource was started or installed.

| Attempt | Exact command | Exit | Result |
|---|---|---:|---|
| Build, 15:36:06 UTC | `docker build --platform linux/arm64 --tag dams-research:local .` | 1 | Daemon socket unavailable |
| Doctor, 15:36:09 UTC | `docker run --rm --network none dams-research:local python -m dams_sim doctor` | 1 | Daemon socket unavailable; Python was not started |
| Smoke, 15:36:09 UTC | `docker run --rm --network none dams-research:local python -m dams_sim smoke --output /work/smoke` | 1 | Daemon socket unavailable; no model output was produced |

All three commands returned this diagnostic (the build prefixed it with `ERROR:`):

```text
failed to connect to the docker API at unix:///var/run/docker.sock; check if the path is correct and if the daemon is running: dial unix /var/run/docker.sock: connect: no such file or directory
```

The upstream registry digests and platform descriptors are verified metadata. A separate static path audit found all **43 individually admitted files** present, no admitted symlinks, all 9 model / 11 research / 4 test Python modules included, and the required font notice included. The inspected `uv.lock` SHA-256 was `ba061682fb1b67f1c91beeccf9f36352dd68a912020662d9b74b376f4239e00c`. This is not a daemon-built context audit or a successful container dependency installation. Application image ID/digest, container test results, Linux runtime correctness, full container reproduction and cross-platform equality remain unmeasured. The recipe is supplied for inspection and later testing; the native evidence is unchanged.
