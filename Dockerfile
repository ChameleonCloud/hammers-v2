FROM python:3

RUN pip install --upgrade pip setuptools wheel

WORKDIR /etc/hammers
COPY . /etc/hammers

RUN pip install .
