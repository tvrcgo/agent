FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY agent/ agent/
RUN for req in agent/skills/*/requirements.txt; do \
      [ -f "$req" ] && uv pip install -r "$req"; \
    done

COPY config.yml ./

CMD ["uv", "run", "python", "-m", "agent"]
