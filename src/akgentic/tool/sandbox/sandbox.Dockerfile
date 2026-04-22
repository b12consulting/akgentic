FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git tree cloc curl bash coreutils ca-certificates \
        build-essential gnupg && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Python tooling
RUN pip install -U pip && \
    pip install --no-cache-dir pytest ruff mypy

# uv (fast Python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Node.js 18
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g npm npx typescript webpack webpack-cli && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

CMD ["sleep", "infinity"]
