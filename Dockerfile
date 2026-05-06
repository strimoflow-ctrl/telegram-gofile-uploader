FROM python:3.11-slim

ARG CACHEBUST=2

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "-u", "main.py"]
