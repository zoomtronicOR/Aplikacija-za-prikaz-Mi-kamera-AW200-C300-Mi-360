#!/usr/bin/env bash
# Prepares a COPY of this CT (restored from a backup onto another Proxmox) to run as an independent
# system at a new site. Run it once, in the copied CT, never on the original:
#
#   bash /opt/xiaomi-cam-hub/scripts/prepare_new_site.sh
#
# Removes what belongs to the old site and keeps the rest:
#   removed: Tailscale identity, cameras and their go2rtc streams/tokens, recordings index, events,
#            sessions, recording storage target, MQTT broker, Caddy site addresses, local
#            recording buffer, old backups (moved aside)
#   kept:    users, Xiaomi account login, custom go2rtc build, AI model, notification rules
#            and other channels, HTTPS root certificate (devices that trust it keep trusting it)
set -euo pipefail
APP_DIR=/opt/xiaomi-cam-hub
cd "$APP_DIR"

ask() { local reply; read -r -p "$1" reply; echo "$reply"; }

echo "== Priprema kopije za novu lokaciju =="
echo "Server: $(hostname), adrese: $(hostname -I | xargs)"
if command -v tailscale >/dev/null && tailscale status >/dev/null 2>&1; then
  echo "Tailscale: $(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"])')"
fi
echo
echo "Podaci u bazi koji bi bili obrisani:"
docker compose exec -T app python -m app.cli reset-site --dry-run
echo
echo "Ovo pokreni SAMO na kopiji na novoj lokaciji. Na originalu bi obrisalo kamere i podešavanja."
[ "$(ask 'Nastavi? Upiši DA: ')" = "DA" ] || { echo "Prekinuto."; exit 1; }

new_ip=$(ask "IP adresa ovog servera na novoj lokaciji [$(hostname -I | awk '{print $1}')]: ")
new_ip=${new_ip:-$(hostname -I | awk '{print $1}')}
new_name=$(ask "Kratko ime servera (za HTTPS i Tailscale), npr. cam-debrc: ")
[[ "$new_name" =~ ^[a-z0-9-]{2,40}$ ]] || { echo "Ime: mala slova, brojevi i crtice."; exit 1; }

echo "-> zaustavljam snimač (da ne snima i ne šalje snimke stare lokacije)"
docker compose stop recorder >/dev/null

echo "-> baza"
docker compose exec -T app python -m app.cli reset-site

echo "-> go2rtc: uklanjam streamove kamera (Xiaomi prijava ostaje)"
python3 - <<'EOF'
import re
p = "config/go2rtc.yaml"
s = open(p).read()
# the streams block runs until the next top-level key
s = re.sub(r"^streams:\n(?:[ \t]+.*\n|\n)*", "streams:\n", s, flags=re.M)
open(p, "w").write(s)
EOF
chmod 600 config/go2rtc.yaml

echo "-> tokeni kamera"
echo '{}' > config/miio_tokens.json
chown 10001:10001 config/miio_tokens.json && chmod 600 config/miio_tokens.json

echo "-> Caddy: nove adrese ($new_ip, $new_name)"
sed -e "s/192\.168\.1\.10/$new_ip/g" -e "s/camera-server/$new_name/g" Caddyfile.example \
  | sed '/^# Copy to config/,/^# through a camera) only works/d' > config/Caddyfile
chmod 644 config/Caddyfile

echo "-> lokalni bafer snimača i stare rezervne kopije"
rm -rf data/recbuf/seg data/recbuf/clips
if [ -d /var/backups/xiaomi-cam-hub ]; then
  mkdir -p /var/backups/xiaomi-cam-hub-old-site
  find /var/backups/xiaomi-cam-hub -maxdepth 1 -name 'xiaomi-cam-hub-*.tar.gz' -exec mv {} /var/backups/xiaomi-cam-hub-old-site/ \;
fi

if command -v tailscale >/dev/null; then
  echo "-> Tailscale: brišem identitet stare lokacije"
  tailscale logout >/dev/null 2>&1 || true
  systemctl stop tailscaled
  rm -rf /var/lib/tailscale/*
  systemctl start tailscaled
fi

echo "-> pokrećem sve servise"
docker compose up -d >/dev/null
docker compose restart go2rtc caddy >/dev/null

cat <<EOF

Gotovo. Sledeći koraci:
  1. Tailscale (ako ga koristiš):  tailscale up --accept-routes --hostname=$new_name
     zatim u Caddyfile dodaj blok za https://$new_name.<tailnet>.ts.net (primer u Caddyfile.example)
     i: docker compose restart caddy
  2. Otvori https://$new_ip  (prijava istim nalozima kao na staroj lokaciji)
  3. Kamere → Xiaomi kamere → Učitaj kamere sa naloga → Dodaj (kamere ove lokacije)
  4. Snimanje → skladište ove lokacije;  Obaveštenja → MQTT ka ovdašnjem Home Assistant-u
  5. Proxmox: backup zadatak za ovaj CT
Stare rezervne kopije su u /var/backups/xiaomi-cam-hub-old-site (možeš ih obrisati).
EOF
