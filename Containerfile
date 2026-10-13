FROM docker.io/library/python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first, so editing source does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --system --create-home --uid 10001 scraper && mkdir -p /data/blobs && chown scraper /data/blobs
USER scraper

ENV SCRAPER_HOSTED=true SCRAPER_HOST=0.0.0.0 SCRAPER_PORT=8080
EXPOSE 8080
CMD ["/app/.venv/bin/python", "-m", "scraper"]
