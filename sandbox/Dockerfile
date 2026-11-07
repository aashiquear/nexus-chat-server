# Multi-language execution sandbox for nexus-chat.
#
# Runtimes: Python 3.12 (+ scientific stack), Node.js 20 (JS/TS), Java 17,
# GCC/G++, Go, Rust, R. Packages can be installed on the fly at run time
# (pip → /sandbox/pylib, npm → per-run node_modules); those directories
# are declared as volumes so installs persist across container restarts.
#
# The container runs as a non-root user with bounded CPU/memory (set in
# docker-compose.yml). Only the nexus-chat backend can reach it.
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg build-essential gcc g++ make \
        openjdk-17-jdk-headless golang-go r-base-core \
        git && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Rust toolchain (minimal profile) available to every user via /usr/local.
RUN curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --no-modify-path && \
    ln -sf /root/.cargo/bin/rustc /usr/local/bin/rustc && \
    ln -sf /root/.cargo/bin/cargo /usr/local/bin/cargo && \
    chmod -R a+rX /root/.cargo /root/.rustup && chmod a+x /root || true

WORKDIR /sandbox

# Server deps + a broad scientific/data stack so most snippets run without
# an install step. Anything else is pip-installed on demand.
RUN pip install --no-cache-dir \
        fastapi==0.115.0 "uvicorn[standard]==0.30.6" pydantic==2.9.0 \
        numpy pandas scipy sympy matplotlib seaborn plotly kaleido \
        scikit-learn statsmodels networkx \
        requests httpx beautifulsoup4 lxml pyyaml openpyxl xlrd \
        pillow tabulate tqdm python-dateutil pyarrow \
        fpdf2 python-docx python-pptx pypdf2 reportlab

COPY server.py /sandbox/server.py

# The run root is root-owned with mode 1733: the sandbox user can create
# and enter run directories (random 128-bit names) but cannot list the
# root or change its permissions, so one run cannot discover another's
# files even though every run executes as the same OS user.
RUN useradd -m -u 10001 sandbox && \
    mkdir -p /sandbox/pylib /sandbox/npm-cache /tmp/nexus-sandbox && \
    chown -R sandbox:sandbox /sandbox && \
    chown root:root /tmp/nexus-sandbox && chmod 1733 /tmp/nexus-sandbox
USER sandbox

ENV SANDBOX_PIP_TARGET=/sandbox/pylib \
    SANDBOX_NPM_CACHE=/sandbox/npm-cache \
    SANDBOX_ROOT=/tmp/nexus-sandbox \
    PATH="/usr/local/bin:/usr/bin:/bin:/usr/local/go/bin:${PATH}"

VOLUME ["/sandbox/pylib", "/sandbox/npm-cache"]
EXPOSE 8500

CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8500"]
