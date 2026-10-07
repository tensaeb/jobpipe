FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements-optional.txt
COPY jobpipe/ ./jobpipe/
COPY samples/ ./samples/
COPY profile.example.yaml selftest.py ./
# Mount at runtime: ./profile.yaml ./cv ./data ./outbox and your .env
VOLUME ["/app/data", "/app/outbox", "/app/cv"]
ENTRYPOINT ["tini", "--", "python", "-m", "jobpipe"]
CMD ["watch"]
