FROM nginx:1.27-alpine

# Адрес бэкенда внутри docker-сети. Запросы /api/* уходят на него без изменения пути.
ENV BACKEND_URL=http://api:8000 \
    DNS_RESOLVER=127.0.0.11

COPY frontend/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY frontend/public/ /usr/share/nginx/html/

EXPOSE 80
