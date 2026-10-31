#!/usr/bin/env bash
# Nightly backup, run inside the LXC by cron (/etc/cron.d/xiaomi-cam-hub-backup).
# Writes one archive per run to $BACKUP_DIR and keeps $KEEP_DAYS days. By default the archives stay
# inside the container, so they only leave it through a Proxmox vzdump of the CT. Set
# BACKUP_DIR to a host bind mount and REQUIRE_MOUNT=1 to keep them outside the container.
#
# Archive contents:
#   db.dump        PostgreSQL custom-format dump (users, cameras, sessions, audit log)
#   .env           app secrets (DB and go2rtc passwords)
#   config/        go2rtc.yaml (Xiaomi login, stream URLs with camera passwords), miio_tokens.json, Caddyfile
#   caddy-pki/     Caddy's local CA (root key: keep the archive private)
#   docker-compose.yml
#
# Restore:
#   tar xzf <archive> -C /opt/xiaomi-cam-hub     (restores .env, config/, docker-compose.yml, db.dump)
#   chown 10001:10001 config/miio_tokens.json && chmod 600 config/miio_tokens.json .env config/go2rtc.yaml
#   docker compose up -d postgres
#   docker compose exec -T postgres pg_restore -U xiaomi_app -d xiaomi_cam --clean --if-exists < db.dump
#   docker compose up -d
#   docker compose cp caddy-pki/. caddy:/data/caddy/pki && docker compose restart caddy   (keeps the same root CA)
# The custom go2rtc image is not in the backup; rebuild it as described in docker-compose.yml.
set -euo pipefail
APP_DIR=${APP_DIR:-/opt/xiaomi-cam-hub}
BACKUP_DIR=${BACKUP_DIR:-/var/backups/xiaomi-cam-hub}
KEEP_DAYS=${KEEP_DAYS:-14}

if [ "${REQUIRE_MOUNT:-0}" = 1 ] && ! mountpoint -q "$BACKUP_DIR"; then
  echo "backup: $BACKUP_DIR is not a mount point, refusing to fill the container disk" >&2
  exit 1
fi

umask 077
mkdir -p "$BACKUP_DIR"
cd "$APP_DIR"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

docker compose exec -T postgres pg_dump -U xiaomi_app -d xiaomi_cam -Fc > "$work/db.dump"
cp -a .env docker-compose.yml "$work/"
mkdir "$work/config"
cp -a config/go2rtc.yaml config/miio_tokens.json "$work/config/"
[ -f config/Caddyfile ] && cp -a config/Caddyfile "$work/config/"
# Caddy's local CA: without it a rebuild makes every device trust a new root certificate
if docker compose ps --services --status running | grep -qx caddy; then
  docker compose cp caddy:/data/caddy/pki "$work/caddy-pki" >/dev/null
fi

out="$BACKUP_DIR/xiaomi-cam-hub-$(date +%Y%m%d-%H%M).tar.gz"
tar czf "$out.tmp" -C "$work" .
mv "$out.tmp" "$out"

find "$BACKUP_DIR" -maxdepth 1 -name 'xiaomi-cam-hub-*.tar.gz' -mtime +"$KEEP_DAYS" -delete
echo "backup: $out ($(du -h "$out" | cut -f1))"
