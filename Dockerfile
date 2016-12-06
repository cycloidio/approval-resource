FROM python:3-alpine

RUN apk update && \
   apk --no-cache add ca-certificates git bash wget unzip

# install requirements
ADD requirements*.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# install asserts
ADD assets/ /opt/resource/

# test
ADD test/ /opt/resource-tests/
#RUN /opt/resource-tests/test.sh
