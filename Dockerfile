FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml README.md config.example.yaml ./
COPY py_scheduler ./py_scheduler
COPY main.py .
RUN pip install --no-cache-dir .
COPY config.example.yaml /app/config/config.yaml

# Sobrescreva com volume em `/app/config/config.yaml` e use `/data` para o SQLite.
RUN mkdir -p /data /app/config
VOLUME ["/data", "/app/config"]

ENV PY_SCHEDULER_JOBS_MODULE=py_scheduler.example_jobs

EXPOSE 9100

# Daemon: processo em primeiro plano até SIGTERM/SIGINT (BlockingScheduler).
CMD ["python", "main.py", "/app/config/config.yaml"]
