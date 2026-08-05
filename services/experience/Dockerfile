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
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
