FROM python:3.9-alpine

RUN apk update
RUN apk add build-base
RUN apk add musl-dev
RUN apk add linux-headers
RUN apk add py3-pip
RUN apk add supervisor
RUN apk add nginx
RUN apk add libpq-dev
RUN apk add cmake
RUN apk add pkgconfig
RUN apk add redis
RUN apk add --no-cache bash

RUN pip3 install --upgrade pip
RUN pip3 install uwsgi
RUN pip3 install flask
RUN pip3 install flask-restful
RUN pip3 install requests
RUN pip3 install celery
RUN pip3 install redis

COPY . ./alcala_microservice/
WORKDIR /alcala_microservice/

RUN apk add openssl
RUN openssl req -x509 -nodes -days 365 -subj "/C=CA/ST=QC/O=AquinasNetwork, Inc./CN=aquinasnetwork.com" -addext "subjectAltName=DNS:aquinasnetwork.com" -newkey rsa:2048 -keyout /etc/ssl/private/nginx-selfsigned.key -out /etc/ssl/certs/nginx-selfsigned.crt

RUN apk add curl
RUN apk add nano

COPY config/nginx.conf /etc/nginx/
COPY config/alcala-ms-nginx.conf /etc/nginx/conf.d/
COPY config/uwsgi.ini /etc/uwsgi/
COPY config/supervisord.conf /etc/

EXPOSE 443

CMD ["supervisord", "--nodaemon", "--configuration", "/etc/supervisord.conf"]
