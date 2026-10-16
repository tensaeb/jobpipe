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
    CMD python -c "import time,pathlib; hf=pathlib.Path('/app/data/watcher_heartbeat'); t=float(hf.read_text()) if hf.exists() else 0; exit(0 if time.time()-t<1800 else 1)" || exit 1
ENTRYPOINT ["tini", "--", "python", "-m", "jobpipe"]
CMD ["watch"]
