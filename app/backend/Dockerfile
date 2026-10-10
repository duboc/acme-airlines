# One image for the Python services; the command picks the service:
#   acme-customer (runtime API + A2A), acme-sales (Sales agent), acme-sim (simulators)
FROM python:3.12-slim AS build
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1
RUN pip install --no-cache-dir uv==0.13.0
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin acme
COPY --from=build --chown=10001:10001 /app /app
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8700 8800 8801
CMD ["acme-customer"]
