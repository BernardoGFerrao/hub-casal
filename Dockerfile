FROM python:3.11-slim

WORKDIR /app

COPY hub/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hub/ ./hub/

EXPOSE 5001

CMD ["python", "hub/server.py"]
