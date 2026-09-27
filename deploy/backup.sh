#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

: "${POSTGRES_USER:?set POSTGRES_USER in deploy/.env}"
: "${POSTGRES_DB:?set POSTGRES_DB in deploy/.env}"

backup_dir="${BACKUP_DIR:-./backups}"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$backup_dir/commerce-brain-${stamp}.dump"

docker compose --env-file .env exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$target"

docker run --rm -i postgres:16-alpine pg_restore --list < "$target" >/dev/null
find "$backup_dir" -type f -name 'commerce-brain-*.dump' -print0 \
  | sort -z -r \
  | tail -z -n +8 \
  | xargs -0 -r rm -f

chmod 600 "$target"
printf 'backup_created=%s\n' "$target"
