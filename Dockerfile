FROM python:3.10-slim
LABEL authors="eitchtee"
LABEL Maintainer="eitchtee"

WORKDIR /usr/gcal2todoist

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY ./src ./

ENTRYPOINT ["python", "gcal2todoist.py"]