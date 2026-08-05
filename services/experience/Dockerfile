FROM node:22-slim AS web-build
WORKDIR /web
COPY services/experience/web/package.json services/experience/web/package-lock.json ./
RUN npm ci
COPY services/experience/web ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

COPY services/experience/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY libs ./libs
COPY services/experience/app ./app
COPY --from=web-build /app/static ./app/static

ENV PYTHONPATH=/app:/app/libs
# --reload-exclude: tests write real plugin .py files into app/plugins/
# at runtime (test_execution_and_export_plugins.py, test_plugin_sdk.py,
# etc.) to prove a plugin registered mid-run loads for real — without
# this exclude, uvicorn's watcher restarts the whole process on every
# such write, which can drop an unrelated in-flight request on an
# adjacent test (a raw "Internal Server Error", never a code bug).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-exclude", "*/plugins/*.py"]
