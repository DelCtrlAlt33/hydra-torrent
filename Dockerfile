FROM python:3.12-slim

WORKDIR /app

COPY requirements-docker.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV HYDRA_BASE=/data

RUN mkdir -p /data/certs /data/downloads_incomplete /data/downloads_complete

EXPOSE 8765

CMD ["python3", "hydra_daemon.py"]
