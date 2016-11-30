FROM python:3-alpine

RUN apk update && \
   apk --no-cache add bash

# install requirements
ADD requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN rm requirements*

# install asserts
ADD assets/ /opt/resource/
