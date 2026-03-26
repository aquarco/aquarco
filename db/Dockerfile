FROM python:3.12-alpine

RUN apk add --no-cache postgresql-dev gcc musl-dev && \
    pip install --no-cache-dir "yoyo-migrations[postgres]" && \
    apk del gcc musl-dev

WORKDIR /db

COPY yoyo.ini /db/yoyo.ini
COPY migrate.sh /db/migrate.sh
RUN chmod +x /db/migrate.sh

COPY migrations/ /db/migrations/

ENTRYPOINT ["/db/migrate.sh"]
CMD ["apply"]
