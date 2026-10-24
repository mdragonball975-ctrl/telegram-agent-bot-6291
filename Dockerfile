FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKSPACE_DIR=/app/workspace \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .
RUN useradd -m -u 10001 agent && mkdir -p /app/workspace && chown -R agent:agent /app
USER agent

EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
  CMD python healthcheck.py || exit 1

CMD ["python", "main.py"]
