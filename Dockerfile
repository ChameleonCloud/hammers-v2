FROM python:3.13-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    python3-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

WORKDIR /opt/hammers
COPY . /opt/hammers

RUN pip install --no-cache-dir .

VOLUME /etc/hammers
VOLUME /var/log

WORKDIR /etc/hammers
