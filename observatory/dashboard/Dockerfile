FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY --from=web /web/dist ./web/dist
RUN uv sync --frozen --no-dev
RUN useradd --system --no-create-home app
USER app
ENV PORT=8080 DASHBOARD_STATIC=/app/web/dist
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn dashboard.app:create_app --factory --host 0.0.0.0 --port ${PORT}"]
