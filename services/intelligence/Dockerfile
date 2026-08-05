FROM python:3.12-slim
WORKDIR /app

COPY services/intelligence/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY libs ./libs
COPY services/intelligence/app ./app

ENV PYTHONPATH=/app:/app/libs
# --reload-exclude: tests write real plugin .py files into app/plugins/
# at runtime (test_execution_and_export_plugins.py, test_plugin_sdk.py,
# etc.) to prove a plugin registered mid-run loads for real — without
# this exclude, uvicorn's watcher restarts the whole process on every
# such write, which can drop an unrelated in-flight request on an
# adjacent test (a raw "Internal Server Error", never a code bug).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-exclude", "*/plugins/*.py"]
