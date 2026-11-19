# syntax=docker/dockerfile:1
# Build context: repository root (see docker-compose.yml). Two targets:
#   server  - Rasa NLU + dialogue, REST channel only, model trained at build time
#   actions - the custom action server (shared interview script)
#
# Rasa Open Source 3.6 runs on Python <= 3.10 only. Python 3.10 is past its
# upstream end of life, so the images use Ubuntu 22.04 LTS, whose python3.10
# package still receives security updates from Canonical.
ARG BASE_IMAGE=ubuntu:22.04

FROM ${BASE_IMAGE} AS base
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends python3.10 python3.10-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10002 rasa \
 && useradd --system --uid 10002 --gid 10002 --no-create-home --shell /usr/sbin/nologin rasa
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    RASA_TELEMETRY_ENABLED=false \
    TF_CPP_MIN_LOG_LEVEL=2

# --- dependencies (hash-locked; installed exactly as listed) -----------------
FROM base AS server-deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python3.10 -m venv /opt/venv
COPY rasa/requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes --no-deps -r /tmp/requirements.txt \
 && pip uninstall -y pip

FROM base AS actions-deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python3.10 -m venv /opt/venv
COPY rasa/actions/requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes --no-deps -r /tmp/requirements.txt \
 && pip uninstall -y pip

# --- train the model from the repository's training data ---------------------
FROM server-deps AS model
WORKDIR /build
COPY rasa/config.yml rasa/domain.yml rasa/credentials.yml rasa/endpoints.yml ./
COPY rasa/data/ data/
COPY rasa/tests/ tests/
RUN rasa data validate --fail-on-warnings \
 && rasa train --fixed-model-name interview --out models \
 && rasa test core --stories tests/test_stories.yml --model models/interview.tar.gz --out /tmp/results --fail-on-prediction-errors

# --- runtime images ------------------------------------------------------------
FROM base AS server
COPY --from=server-deps /opt/venv /opt/venv
WORKDIR /app
COPY --from=model /build/models/interview.tar.gz models/interview.tar.gz
COPY rasa/credentials.yml rasa/endpoints.yml ./
USER rasa
EXPOSE 5005
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
  CMD ["python3.10", "-c", "import sys,urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:5005/', timeout=3).status != 200)"]
# No --enable-api: only the REST channel webhook and "/" are served, so the
# model-management HTTP API is not reachable at all. No --cors either.
CMD ["rasa", "run", "--model", "models/interview.tar.gz", "--endpoints", "endpoints.yml", \
     "--credentials", "credentials.yml", "--interface", "0.0.0.0", "--port", "5005"]

FROM base AS actions
COPY --from=actions-deps /opt/venv /opt/venv
WORKDIR /app
COPY interview/ interview/
COPY rasa/actions/ actions/
ENV PYTHONPATH=/app
USER rasa
EXPOSE 5055
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python3.10", "-c", "import sys,urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:5055/health', timeout=2).status != 200)"]
CMD ["python3.10", "-m", "rasa_sdk", "--actions", "actions", "--port", "5055"]
