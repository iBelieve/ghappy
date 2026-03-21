FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project

COPY ghappy/ ghappy/
RUN uv sync --no-dev

RUN useradd -r -m -s /bin/false appuser
USER appuser

EXPOSE 8000

CMD ["uv", "run", "ghappy-server"]
