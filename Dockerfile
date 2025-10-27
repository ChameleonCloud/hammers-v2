FROM python:3.12

COPY . /opt/hammers
WORKDIR /opt/hammers

RUN pip install .

LABEL org.opencontainers.image.source = "https://github.com/ChameleonCloud/hammers-v2"
