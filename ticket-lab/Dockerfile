ARG PYTHON_BASE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE}

ARG APP_VERSION=0.1.0
ARG VCS_REF=unrecorded
LABEL org.opencontainers.image.title="Ticket Lab" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION}
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY ops/ ./ops/
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
