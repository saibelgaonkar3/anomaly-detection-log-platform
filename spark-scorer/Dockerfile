FROM python:3.11-slim

# Spark needs a JVM
RUN apt-get update && \
    apt-get install -y --no-install-recommends default-jre-headless curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Postgres JDBC driver so Spark can read/write Postgres directly
RUN curl -o /app/postgresql-42.7.3.jar \
    https://jdbc.postgresql.org/download/postgresql-42.7.3.jar

COPY score.py .

CMD ["python", "score.py"]
