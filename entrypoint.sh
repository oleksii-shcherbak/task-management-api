#!/bin/sh
set -e

until pg_isready -h "${POSTGRES_HOST:-postgres}" -q; do
  echo "Waiting for postgres..."
  sleep 1
done

alembic upgrade head

exec "$@"
