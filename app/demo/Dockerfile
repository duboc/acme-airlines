# Single-container IRROPS demo: built frontend + runtime API + acme-sim + Sales agent.
# Build context is `app/`:  docker build -f app/demo/Dockerfile app
# One public port ($PORT, default 8080). /api/a2a, /.well-known and /mcp/* answer 404; the
# simulators, Customer runtime and Sales agent listen on loopback only. Sim tokens are generated at
# startup and never logged. LLM_MODE defaults to `fake`.
FROM node:22-alpine AS web
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npx vite build

FROM python:3.12-slim AS build
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1
RUN pip install --no-cache-dir uv==0.13.0
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin acme
COPY --from=build --chown=10001:10001 /app /app
COPY --from=web --chown=10001:10001 /app/dist /app/static
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 LLM_MODE=fake PORT=8080
USER 10001
EXPOSE 8080
CMD ["acme-demo"]
