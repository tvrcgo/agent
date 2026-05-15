FROM node:22-slim

ENV TZ=Asia/Shanghai

# Install Python for the HTTP server
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml ./
RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir fastapi uvicorn httpx pyyaml

COPY mcp_server.py config.yml ./

# Create workspace directory for filesystem MCP
RUN mkdir -p /workspace

ENV PATH="/opt/venv/bin:$PATH"
ENV MCP_CONFIG=/app/config.yml

EXPOSE 8001

CMD ["python3", "mcp_server.py"]
