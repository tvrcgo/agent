FROM python:3.12-slim

RUN pip install uv --no-cache-dir

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY agent/ agent/
RUN for req in agent/tools/*/requirements.txt; do \
      [ -f "$req" ] && uv pip install -r "$req"; \
    done

COPY config.yml ./

CMD ["uv", "run", "python", "-m", "agent"]
