FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY monitor ./monitor
COPY simulator ./simulator
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "monitor.app"]
