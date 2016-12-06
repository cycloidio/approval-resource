FROM python:3-alpine

RUN apk update && \
   apk --no-cache add bash

# install requirements
ADD requirements*.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -r requirements-dev.txt

RUN rm requirements*