FROM python:3.12-slim

RUN apt-get update && \
    apt-get -qy install curl && \
    curl -sSL https://get.docker.com/ | sh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY bot.py bot.py
COPY spotifyFunction.py spotifyFunction.py
COPY krillion.py krillion.py
COPY GIT_SHA GIT_SHA

CMD python bot.py
