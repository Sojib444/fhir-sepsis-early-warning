# Build context is the repository root (docker-compose sets it). The image
# needs the trained artifacts present (results/models/, results/threshold.json),
# which exist once `make train` has run — see README.md.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml config.yaml ./
COPY src/sepsis ./src/sepsis
COPY src/model_api ./src/model_api

# Binary wheels only: shap/numba/llvmlite compile from source on slim would need
# a toolchain nobody wants in a serving image. Fail loudly if a wheel is missing.
RUN pip install --no-cache-dir --only-binary :all: .

# Frozen artifacts the service loads at startup (MODEL_FILE/THRESHOLD_DECISION
# select the matrix cell being served; defaults: A→A model, utility-max threshold).
COPY results/models ./results/models
COPY results/threshold.json ./results/threshold.json

ENV MODEL_FILE=window_a.txt \
    THRESHOLD_DECISION=A

EXPOSE 8000
CMD ["uvicorn", "model_api.main:app", "--host", "0.0.0.0", "--port", "8000"]