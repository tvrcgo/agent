FROM python:3.12-slim

RUN pip install uv --no-cache-dir

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY agent/tools/*/requirements.txt /tmp/tool-reqs/
RUN find /tmp/tool-reqs -name 'requirements.txt' -exec uv pip install -r {} \;

COPY agent/ agent/

COPY config.yml ./

CMD ["uv", "run", "python", "-m", "agent"]
