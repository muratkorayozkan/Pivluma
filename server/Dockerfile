FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir 'gunicorn>=23,<24'
COPY cloud_server.py ./
RUN mkdir /data && chown 65532:65532 /data
USER 65532:65532
ENV PIVLUMA_QUOTA_DB=/data/pivluma-quota.sqlite3
EXPOSE 8080
CMD ["gunicorn", "--bind=0.0.0.0:8080", "--workers=2", "--threads=4", "--timeout=60", "cloud_server:application"]
