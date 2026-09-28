FROM python:3.11-slim

ARG APP_VERSION=dev
ARG GIT_SHA=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/bridge \
    APP_VERSION=${APP_VERSION} \
    GIT_SHA=${GIT_SHA}

LABEL org.opencontainers.image.title="Commerce Brain Control Plane" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}"

WORKDIR /app

COPY bridge/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY bridge /app/bridge

EXPOSE 8000

CMD ["uvicorn", "control_plane.production:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
