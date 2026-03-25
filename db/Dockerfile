FROM python:3.12-alpine

RUN pip install --no-cache-dir "yoyo-migrations[postgres]"

WORKDIR /db

COPY yoyo.ini /db/yoyo.ini
COPY migrate.sh /db/migrate.sh
RUN chmod +x /db/migrate.sh

COPY migrations/ /db/migrations/

ENTRYPOINT ["/db/migrate.sh"]
CMD ["apply"]
