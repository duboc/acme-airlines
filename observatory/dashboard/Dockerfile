FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
# web/dist (the built UI, slice 5) is added here once it exists; the app serves it if present.
RUN uv sync --frozen --no-dev
RUN useradd --system --no-create-home app
USER app
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn dashboard.app:create_app --factory --host 0.0.0.0 --port ${PORT}"]
