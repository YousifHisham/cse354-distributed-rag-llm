#!/bin/sh
# Substitute env vars into the NGINX config template, then start supervisord.
set -e

envsubst '${COORDINATOR_PORT} ${NGINX_WORKER_CONNECTIONS} ${NGINX_PROXY_READ_TIMEOUT}' \
    < /etc/nginx/nginx.conf.template \
    > /etc/nginx/nginx.conf

exec supervisord -c /etc/supervisord.conf
