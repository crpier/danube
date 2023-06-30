from python:3.11.4-slim

RUN apt-get update && apt-get install -y curl
RUN mkdir -p /home/danube/danube

ADD . /home/danube

WORKDIR /home/danube
ENV POETRY_VIRTUALENVS_IN_PROJECT=true
ENV POETRY_VERSION=1.5.0
ENV PATH="~/.local/bin:$PATH"
RUN curl -sSL https://install.python-poetry.org | python3 -
RUN ~/.local/bin/poetry install --no-interaction --no-ansi -vvv

ENTRYPOINT env PYTHONPATH=/home/danube .venv/bin/python -u /home/danubefile.py
