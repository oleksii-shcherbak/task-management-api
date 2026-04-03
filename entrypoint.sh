#!/bin/sh
set -e

until python3 -c "
import socket, os, sys
host = os.environ.get('POSTGRES_HOST', 'postgres')
try:
    socket.create_connection((host, 5432), timeout=1).close()
    sys.exit(0)
except OSError:
    sys.exit(1)
" 2>/dev/null; do
  echo "Waiting for postgres..."
  sleep 1
done

alembic upgrade head

exec "$@"
