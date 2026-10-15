FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash jobpipe
COPY requirements.txt requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements-optional.txt
COPY jobpipe/ ./jobpipe/
COPY samples/ ./samples/
COPY profile.example.yaml selftest.py ./
# Mount at runtime: ./profile.yaml ./cv ./data ./outbox and your .env
VOLUME ["/app/data", "/app/outbox", "/app/cv"]
USER jobpipe
HEALTHCHECK --interval=30m --timeout=10s --start-period=5m --retries=3 \
    CMD python -c "import sqlite3; sqlite3.connect('/app/data/jobpipe.db')" || exit 1
ENTRYPOINT ["tini", "--", "python", "-m", "jobpipe"]
CMD ["watch"]
