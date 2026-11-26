FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY run_fixtures.py ./run_fixtures.py
ENV SHIPGATE_DB=/data/shipgate.db
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "11800"]
