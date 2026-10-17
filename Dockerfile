FROM python:3.11-slim
WORKDIR /app
COPY . /app
RUN wheel="$(find /app/vendor -name 'keel_sdk-*.whl' -print -quit)" \
 && test -n "$wheel" \
 && pip install --no-cache-dir "$wheel" -r requirements.lock \
 && rm -rf /app/vendor
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
