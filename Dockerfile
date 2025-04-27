FROM python:3.12-slim

WORKDIR /app
COPY app /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

EXPOSE 8000
CMD ["python", "app.py"]