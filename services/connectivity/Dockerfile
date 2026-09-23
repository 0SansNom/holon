FROM python:3.12-slim
WORKDIR /app

# FreeTDS + unixODBC for the SQL Server (mssql) driver via aioodbc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    unixodbc unixodbc-dev tdsodbc freetds-bin freetds-dev \
    && rm -rf /var/lib/apt/lists/* \
    && DRIVER=$(find /usr -name 'libtdsodbc.so' | head -n1) \
    && SETUP=$(find /usr -name 'libtdsS.so' | head -n1) \
    && test -n "$DRIVER" \
    && printf '%s\n' \
        '[FreeTDS]' \
        'Description=FreeTDS Driver' \
        "Driver=${DRIVER}" \
        "Setup=${SETUP}" \
        'FileUsage=1' \
        > /etc/odbcinst.ini

COPY services/connectivity/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY libs ./libs
COPY services/connectivity/app ./app

ENV PYTHONPATH=/app:/app/libs

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
