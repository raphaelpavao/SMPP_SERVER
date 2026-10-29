#!/bin/sh
set -e

python manage.py migrate --noinput

# Primeiro acesso: cria o administrador a partir do .env, se ainda não existir.
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
    python manage.py createsuperuser --noinput --email "${DJANGO_SUPERUSER_EMAIL:-}" 2>/dev/null \
        && echo "Administrador $DJANGO_SUPERUSER_USERNAME criado." \
        || true
fi

exec "$@"
