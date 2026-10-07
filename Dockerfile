# Optional CPU research packaging. Local container execution is not certified;
# see docs/CONTAINER.md for the actual attempt and immutable upstream identities.
FROM ghcr.io/astral-sh/uv:0.9.26@sha256:9a23023be68b2ed09750ae636228e903a54a05ea56ed03a934d00fe9fbeded4b AS uv
FROM python:3.14.2-slim@sha256:1a3c6dbfd2173971abba880c3cc2ec4643690901f6ad6742d0827bae6cefc925

ARG SOURCE_REVISION=unrecorded
LABEL org.opencontainers.image.title="DAMS CPU research reference" \
      org.opencontainers.image.source="https://github.com/rui-h-a/dams-simulation" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.revision="${SOURCE_REVISION}"

COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/dams-matplotlib \
    XDG_CACHE_HOME=/tmp/dams-cache
WORKDIR /app

# The context is an explicit file allowlist in .dockerignore. Raw research
# evidence, existing environments and private source material are not copied.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY dams_sim/ ./dams_sim/
COPY research_tools/ ./research_tools/
COPY tests/ ./tests/
COPY configs/ ./configs/
COPY docs/ ./docs/

RUN python -c "import sys; assert sys.version_info[:3] == (3, 14, 2), sys.version" \
    && uv --version \
    && uv sync --locked --extra analysis --no-dev --no-cache --python /usr/local/bin/python \
    && mkdir -p /work \
    && chown 10001:10001 /work
ENV PATH="/app/.venv/bin:${PATH}"
USER 10001:10001
CMD ["python", "-m", "dams_sim", "doctor"]
