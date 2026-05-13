#!/bin/sh
# Substitute env vars into the NGINX config template, then start supervisord.
set -e

: "${COORDINATOR_PORT:=8000}"
: "${NGINX_WORKER_CONNECTIONS:=4096}"
: "${NGINX_PROXY_READ_TIMEOUT:=86400}"
export COORDINATOR_PORT NGINX_WORKER_CONNECTIONS NGINX_PROXY_READ_TIMEOUT

envsubst '${COORDINATOR_PORT} ${NGINX_WORKER_CONNECTIONS} ${NGINX_PROXY_READ_TIMEOUT}' \
    < /etc/nginx/nginx.conf.template \
    > /etc/nginx/nginx.conf

exec supervisord -c /etc/supervisord.conf
