FROM python:3.11-slim-bookworm AS build
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
COPY ml/ ml/
RUN test -f ml/model.joblib || /opt/venv/bin/python -m ml.train

FROM python:3.11-slim-bookworm
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SQLITE_PATH=/data/explainer.sqlite
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY *.py rules.yaml ./
COPY ml/*.py ml/
COPY --from=build /build/ml/model.joblib ml/model.joblib
RUN mkdir /data && chown 10001:10001 /data
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
