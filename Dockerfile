FROM python:3.14-slim

WORKDIR /app

COPY requirements-docker.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# GeoIP database for peer/VPN country flags. Not part of the image build
# context (see .dockerignore + fetch_geoip.py) since it's not something
# to commit or copy from the host - fetched fresh at build time instead.
RUN python3 fetch_geoip.py

ENV HYDRA_BASE=/data

RUN mkdir -p /data/certs /data/downloads

EXPOSE 8765

CMD ["python3", "hydra_daemon.py"]
