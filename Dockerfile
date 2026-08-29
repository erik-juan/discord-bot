FROM python:3.7.12-buster

RUN apt-get update && \
    apt-get -qy full-upgrade && \
    apt-get install -qy curl && \
    curl -sSL https://get.docker.com/ | sh

COPY requirements.txt requirements.txt
RUN ["python3", "-m", "pip", "install", "-r", "requirements.txt"]

COPY bot.py bot.py
COPY spotifyFunction.py spotifyFunction.py
COPY krillion.py krillion.py

CMD python bot.py