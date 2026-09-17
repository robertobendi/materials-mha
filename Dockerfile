FROM python:3.12-slim

WORKDIR /app

# Bind every interface so a platform proxy can reach us; the host sets PORT.
# MC_HOST stays on loopback for local runs.
ENV MC_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1

COPY app ./app
COPY scripts ./scripts
COPY rules ./rules
COPY fixtures/base_clean.json ./fixtures/base_clean.json

EXPOSE 8000

CMD ["python", "app/serve.py"]
