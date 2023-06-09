from python:3.11.4-slim

RUN mkdir -p /home/danube/danube

ADD danube/ /home/danube/danube

ENTRYPOINT env PYTHONPATH=/home/danube ython /home/danubefile.py
