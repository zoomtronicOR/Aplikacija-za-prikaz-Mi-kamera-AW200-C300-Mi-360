# Xiaomi Camera Hub

Privatna web aplikacija za prikaz uživo i upravljanje Xiaomi kamerama (AW200, C300, Mi 360° 1080p), ESP32-CAM kamerama i običnim IP kamerama, iz jednog interfejsa u kućnoj mreži. Radi na Proxmox LXC kontejneru, pod Docker Compose-om. Video ide lokalno, bez Xiaomi cloud-a: cloud je potreban samo za prijavu i preuzimanje ključeva.

## Mogućnosti

- **Prikaz uživo** svih kamera na jednoj stranici (WebRTC, MSE ili MJPEG, browser sam bira), prikaz preko celog ekrana i zvuk.
- **Izbor HD/SD** za svaku kameru, posebno na svakom uređaju (npr. telefon na SD, računar na HD).
- **Kontrole kamera** preko lokalne mreže:
  - Xiaomi: uključi/uspavaj, noćni režim, okretanje slike, vremenski žig, lampica, WDR, kolor noću, praćenje pokreta, restart;
  - ESP32-CAM: rezolucija, kvalitet, osvetljenost, kontrast, zasićenost, okretanje i ogledalo, automatski balans bele i ekspozicija.
- **Okretanje (PTZ)** za C300 i Mi 360, strelicama preko slike (potrebna je go2rtc verzija sa PTZ izmenom, vidi niže).
- **Dodavanje drugih kamera** iz interfejsa: RTSP, ONVIF, RTMP i HTTP/MJPEG, uz proveru veze pre čuvanja. ESP32-CAM se prepoznaje automatski i dobija kontrole.
- **Korisnici i uloge:**
  - administrator: sve;
  - korisnik: prikaz i kontrole;
  - pregled: samo prikaz.

  Lozinke su zaštićene Argon2id heširanjem. Uzastopni neuspešni pokušaji prijave privremeno se blokiraju. Prijave i izmene beleže se u audit log.
- **Noćna rezervna kopija** baze i konfiguracije.

## Podržane kamere

| Kamera | Model ID | Slika | Kontrole | PTZ |
|---|---|---|---|---|
| Xiaomi Outdoor Camera AW200 | `isa.camera.hlc9a` | 1920×1080 | MIoT | — |
| Xiaomi Smart Camera C300 | `xiaomi.camera.c01a01` | 2304×1296 | MIoT | ✅ |
| Mi 360° Home Security Camera 1080p | `chuangmi.camera.ipc019` | 1920×1080 | miIO (stariji protokol) | ✅ |
| ESP32-CAM (Espressif `CameraWebServer`) | — | do 1600×1200 | HTTP | — |
| RTSP / ONVIF / HTTP kamere | — | zavisi od kamere | — | — |

Druge Xiaomi kamere mogu raditi preko go2rtc-a za sliku, ali kontrole moraju da se dodaju za svaki model posebno (`app/services/controls.py`).

## Kako radi

```
Xiaomi kamere ──Xiaomi CS2 (šifrovano, lokalno)──┐
ESP32 / IP kamere ──RTSP/HTTP──────────────────── go2rtc ──WebRTC (UDP/TCP 8555)──> browser
                                                    ▲
                                                    │ API + WebSocket (127.0.0.1:1984)
Xiaomi kamere ◄──miIO (UDP 54321)── FastAPI aplikacija (:8080) ── PostgreSQL
ESP32 ◄──HTTP /control─────────────┘         ▲
                                             └── browser (prijava, kontrole, video signalizacija)
```

- **[go2rtc](https://github.com/AlexxIT/go2rtc)** prima video sa svih kamera i prosleđuje ga browseru. Za Xiaomi kamere koristi njihov šifrovani lokalni protokol.
- **Aplikacija** proverava prijavu i prosleđuje video vezu ka go2rtc-u. Browser nikad ne pristupa go2rtc API-ju direktno.
- **Kontrole** idu direktno ka kamerama, lokalnim miIO protokolom za Xiaomi i HTTP-om za ESP32. Za Xiaomi kamere potreban je token svakog uređaja.
- **Lozinke iz adresa kamera** i Xiaomi tokeni čuvaju se samo u fajlovima na serveru (`config/`, `chmod 600`), nikad u bazi ni u browseru.

## Instalacija

Primer je dat za Proxmox LXC kontejner sa Debianom 13. Radi i na bilo kom Linux serveru sa Dockerom.

### 1. Kontejner i Docker

- Napravi **unprivileged** LXC sa Debianom 13 (dovoljno je 2–4 vCPU, 2 GB RAM-a i 20 GB diska).
- Uključi `nesting=1` i `keyctl=1`:

  ```bash
  pct set <CTID> -features nesting=1,keyctl=1
  ```

- Instaliraj [Docker Engine i Compose plugin](https://docs.docker.com/engine/install/debian/).

Kamere i server treba da budu u istoj mreži, jer kontejner mora da dopre do kamera preko UDP-a.

### 2. Kod i konfiguracija

```bash
git clone https://github.com/zoomtronicOR/Aplikacija-za-prikaz-Mi-kamera-AW200-C300-Mi-360.git /opt/xiaomi-cam-hub
cd /opt/xiaomi-cam-hub
mkdir -p config
cp go2rtc.example.yaml config/go2rtc.yaml      # postavi lozinku u api: password, obriši primere streamova
cp .env.example .env                           # postavi lozinke (GO2RTC_PASSWORD = lozinka iz go2rtc.yaml)
chmod 600 .env config/go2rtc.yaml
```

### 3. go2rtc sa podrškom za okretanje (PTZ)

Okretanje Xiaomi kamera još nije u zvaničnom go2rtc-u. Rešava ga [predlog izmene #2531](https://github.com/AlexxIT/go2rtc/pull/2531), pa se go2rtc gradi lokalno:

```bash
mkdir -p /opt/build && cd /opt/build
git clone https://github.com/AlexxIT/go2rtc.git go2rtc-ptz && cd go2rtc-ptz
git checkout -b v1.9.15-ptz v1.9.15
git fetch https://github.com/bcamarneiro/go2rtc.git xiaomi-ptz
git -c user.name=build -c user.email=build@localhost cherry-pick 1a056d7 e23a21b
docker build -f docker/Dockerfile -t go2rtc-local:1.9.15-ptz-e23a21b .
```

Bez okretanja: u `docker-compose.yml` zameni image sa `alexxit/go2rtc:1.9.15` i obriši red `pull_policy: never`. Strelice će tada javljati da okretanje nije podržano, a sve ostalo radi.

### 4. Prijava na Xiaomi nalog i dodavanje kamera u go2rtc

```bash
cd /opt/xiaomi-cam-hub
docker compose up -d go2rtc
```

go2rtc web interfejs je dostupan samo sa samog servera. Sa svog računara otvori SSH tunel:

```bash
ssh -L 1984:127.0.0.1:1984 root@<server>
```

Zatim:

1. Otvori http://localhost:1984 i prijavi se korisnikom `admin` i lozinkom iz `go2rtc.yaml`.
2. Idi na **Add → Xiaomi**, prijavi se na Xiaomi nalog i izaberi region (Europe = `de`).
3. Učitaj listu uređaja i za svaku kameru prepiši njenu `xiaomi://…` adresu u `config/go2rtc.yaml` pod `streams:`. Struktura je ista kao u `go2rtc.example.yaml`.
4. Dodaj parametre za model:

| Model | HD stream | SD stream (`<ime>_sd`) |
|---|---|---|
| AW200 | `&subtype=3` | `&subtype=1` |
| C300 | `&subtype=3` (`hd` ne radi) | `&subtype=1` |
| Mi 360 1080p | bez parametra + `ffmpeg:<ime>#audio=opus` | `&subtype=1` + `ffmpeg:<ime>_sd#audio=opus` |

Na kraju restartuj go2rtc: `docker compose restart go2rtc`.

### 5. Tokeni kamera (za kontrole)

Token preuzima program `scripts/xiaomi-tokens`, koji koristi go2rtc-ovu Xiaomi prijavu, pa nova prijava nije potrebna. Tokeni se ne ispisuju na ekran:

```bash
cd /opt/xiaomi-cam-hub
U=<Xiaomi ID iz go2rtc.yaml, sekcija xiaomi:>
T=$(grep -m1 "\"$U\":" config/go2rtc.yaml | sed -E "s/^[^:]*\"$U\": *//")
docker run --rm -e XIAOMI_USER=$U -e XIAOMI_PASSTOKEN="$T" -e XIAOMI_REGION=de \
  -v "$PWD/scripts/xiaomi-tokens:/src" -v "$PWD/config:/out" -w /src golang:1.26-bookworm \
  sh -c "go mod init xiaomi-tokens >/dev/null 2>&1; go get github.com/AlexxIT/go2rtc@v1.9.15 >/dev/null 2>&1 && go run . /out/miio_tokens.json <did1>,<did2>,<did3>"
chown 10001:10001 config/miio_tokens.json && chmod 600 config/miio_tokens.json
```

DID svake kamere je u njenoj `xiaomi://` adresi (`did=…`).

### 6. Pokretanje aplikacije

```bash
docker compose up -d --build
docker compose exec app python -m app.cli create-user <ime> admin      # lozinka se unosi interaktivno, min. 12 znakova
docker compose exec app python -m app.cli import-cameras               # uvozi Xiaomi kamere iz go2rtc.yaml
```

Aplikacija je na `http://<server>:8080`.

### 7. Noćna rezervna kopija

```bash
cat > /etc/cron.d/xiaomi-cam-hub-backup <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
30 3 * * * root /opt/xiaomi-cam-hub/scripts/backup.sh >> /var/log/xiaomi-cam-hub-backup.log 2>&1
EOF
```

Arhive se čuvaju 14 dana u `/var/backups/xiaomi-cam-hub`. Uputstvo za vraćanje je na početku `scripts/backup.sh`. Arhive sadrže lozinke i tokene, pa ih čuvaj kao poverljive.

## Dodavanje drugih kamera

Kao administrator otvori **Kamere** (dugme u gornjem meniju) i popuni formu. Dugme **Proveri vezu** pre čuvanja pokaže da li slika stiže i koja je rezolucija.

| Kamera | Adresa | Format |
|---|---|---|
| ESP32-CAM | `http://IP:81/stream` | MJPEG preko HTTP-a |
| RTSP kamera | `rtsp://korisnik:lozinka@IP:554/putanja` | Direktno |
| ONVIF kamera | `onvif://korisnik:lozinka@IP` | Direktno |
| Kamera čiji video browser ne prikazuje | bilo koja od gornjih | Pretvori u H.264 (troši procesor) |

Dozvoljene su samo mrežne adrese (`rtsp`, `rtsps`, `rtmp`, `http`, `https`, `onvif`). go2rtc izvori koji izvršavaju komande na serveru (`exec:` i slični) su blokirani.

## Rad i održavanje

```bash
bash scripts/deploy.sh                     # sa razvojnog računara: prenese kod i ponovo izgradi aplikaciju
docker compose logs -f app                 # log aplikacije
docker compose exec app python -m app.cli create-user <ime> <admin|user|viewer>   # novi korisnik ili nova lozinka
```

Migracije baze (Alembic) pokreću se same pri startu aplikacije.

## Bezbednost

- Aplikacija je namenjena **lokalnoj mreži**. Za pristup spolja koristi VPN (npr. Tailscale) ili HTTPS reverse proxy sa prijavom. Port 8080 nemoj izlagati internetu.
- go2rtc API (1984), RTSP (8554) i baza (5432) slušaju samo na `127.0.0.1`. Na mreži su otvoreni samo aplikacija (8080) i WebRTC video (8555).
- Ne šalji u git i ne deli fajlove `.env`, `config/go2rtc.yaml` (sadrži Xiaomi prijavu) i `config/miio_tokens.json`.
- Kolačić sesije ima `secure` zaštitu tek kad se uključi HTTPS (`COOKIE_SECURE=true` u `.env`).

## Poznata ograničenja

- Okretanje radi samo dok je kamera otvorena na ekranu, jer komanda ide kroz aktivnu video vezu.
- go2rtc odgovara „uspešno” i kada kamera nema motor, pa odgovor ne dokazuje pokret.
- Govor ka kameri (dvosmerni zvuk) zahteva HTTPS i još nije uključen.
- Istovremeno gledanje iste Xiaomi kamere kroz Mi Home aplikaciju može izazvati prekide.
- ESP32 `CameraWebServer` primenjuje novu rezoluciju tek na novoj stream vezi. Aplikacija zato ponovo poveže plejer, a drugi gledaoci vide promenu kad osveže stranicu.
- Desilo se da AW200 prestane da odgovara na video vezu. Restart kamere (isključivanje iz struje) je to rešio.

## Licenca

[MIT](LICENSE) © 2026 Zoran Nikolić
