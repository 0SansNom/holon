FROM python:3.12-slim
WORKDIR /app

COPY services/identity/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY libs ./libs
COPY services/identity/app ./app

ENV PYTHONPATH=/app:/app/libs

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
