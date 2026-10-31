# Xiaomi Camera Hub

Privatna web aplikacija za prikaz uživo i upravljanje Xiaomi kamerama (AW200, C300, Mi 360° 1080p), ESP32-CAM kamerama i običnim IP kamerama, iz jednog interfejsa u kućnoj mreži. Radi na Proxmox LXC kontejneru, pod Docker Compose-om. Video ide lokalno, bez Xiaomi cloud-a: cloud je potreban samo za prijavu i preuzimanje ključeva.

## Mogućnosti

- **Prikaz uživo** svih kamera na jednoj stranici (WebRTC, MSE ili MJPEG, browser sam bira), prikaz preko celog ekrana i zvuk. Klik na ime kamere otvara **stranicu kamere**: veliki prikaz, sve kontrole, okretanje strelicama na tastaturi, REC, **slika (JPEG) za preuzimanje** i poslednji snimci te kamere.
- **Izbor HD/SD** za svaku kameru, posebno na svakom uređaju (npr. telefon na SD, računar na HD).
- **Kontrole kamera** preko lokalne mreže:
  - Xiaomi: uključi/uspavaj, noćni režim, okretanje slike, vremenski žig, lampica, WDR, kolor noću, praćenje pokreta, restart;
  - ESP32-CAM: rezolucija, kvalitet, osvetljenost, kontrast, zasićenost, okretanje i ogledalo, automatski balans bele i ekspozicija.
- **Okretanje (PTZ)** za C300 i Mi 360, strelicama preko slike (potrebna je go2rtc verzija sa PTZ izmenom, vidi niže).
- **Dodavanje Xiaomi kamera iz aplikacije:** prijava na Xiaomi nalog, spisak kamera i dodavanje jednim klikom, sa automatskim profilima slike, tokenom i kontrolama, i za modele koje aplikacija ranije nije poznavala.
- **Dodavanje drugih kamera** iz interfejsa: RTSP, ONVIF, RTMP i HTTP/MJPEG, uz proveru veze pre čuvanja. ESP32-CAM se prepoznaje automatski i dobija kontrole.
- **Snimanje** na Windows deljeni folder ili NAS (SMB). Može ručno (dugme REC), stalno (24/7), po rasporedu ili **na pokret**: otporno na drveće na vetru i noćni šum, sa zonama za ignorisanje i 10 s snimka pre pokreta. Stari snimci se automatski brišu. Snimci se pregledaju, puštaju i preuzimaju u aplikaciji.
- **Obaveštenja** preko Telegrama, ntfy-a, emaila i Home Assistant-a (MQTT): o pokretu (sa slikom), o nedostupnoj kameri i njenom povratku, o problemu sa snimanjem. Postoje razmak između poruka i tihi sati, a svaka opcija ima svoj prekidač.
- **Korisnici i uloge:**
  - administrator: sve;
  - korisnik: prikaz i kontrole;
  - pregled: samo prikaz.

  Administrator dodaje i menja korisnike u aplikaciji (stranica **Korisnici**), uz početnu lozinku i obaveznu promenu pri prvoj prijavi. Na istoj stranici je i **dnevnik** (audit log) prijava i izmena. Svako menja svoju lozinku na stranici **Moj nalog**.

  Lozinke su zaštićene Argon2id heširanjem. Uzastopni neuspešni pokušaji prijave privremeno se blokiraju. Prijave i izmene beleže se u audit log.
- **Arhiva događaja:** traka dana po kameri i spisak događaja (pokret sa AI oznakom, nedostupnost, problemi sa snimanjem), sa slikom trenutka i dugmetom koje pušta snimak baš od tog trenutka.
- **Stranica Sistem:** procesor, RAM, disk servera i disk sa snimcima, stanje svih servisa i kamera, uz **alarm za disk** (upozorenje na 80 % i 90 %, pragovi podesivi, sa prekidačem).
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
# ključ za šifrovanje lozinke deljenog foldera; upiši ga u .env kao APP_SECRET_KEY
python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
chmod 600 .env config/go2rtc.yaml
mkdir -p data/recbuf && chown 10001:10001 data/recbuf && chmod 700 data/recbuf   # lokalni bafer snimača
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

### 4. Pokretanje aplikacije

```bash
cd /opt/xiaomi-cam-hub
echo '{}' > config/miio_tokens.json && chown 10001:10001 config/miio_tokens.json && chmod 600 config/miio_tokens.json
docker compose up -d --build
docker compose exec app python -m app.cli create-user <ime> admin      # lozinka se unosi interaktivno, min. 12 znakova
```

Aplikacija je na `http://<server>:8080` dok ne uključiš HTTPS (sledeći korak).

### 5a. HTTPS (preporučeno; potrebno za govor kroz kameru)

```bash
cp Caddyfile.example config/Caddyfile        # zameni 192.168.1.10 i camera-server svojom IP adresom i imenom servera
echo "APP_HOST=127.0.0.1" >> .env              # aplikacija tada sluša samo za Caddy
sed -i "s/^COOKIE_SECURE=false/COOKIE_SECURE=true/" .env
docker compose up -d
```

Aplikacija je tada na `https://<server>`, a http se preusmerava na https. Caddy izdaje sertifikat iz svog lokalnog sertifikacionog tela. Da browser ne bi prikazivao upozorenje (i da bi mikrofon radio), na svaki uređaj jednom instaliraj korenski sertifikat sa `http://<server>/ca.crt`:

| Uređaj | Postupak |
|---|---|
| Windows | Otvori `ca.crt` → *Install Certificate* → *Local Machine* → *Place all certificates in…* → **Trusted Root Certification Authorities**, pa restartuj browser |
| Android | Podešavanja → Bezbednost → Šifrovanje i akreditivi → *Instaliraj sertifikat* → **CA sertifikat** → izaberi preuzeti `ca.crt` |
| iPhone / iPad | Otvori `http://<server>/ca.crt` u Safariju → Podešavanja → *Profil preuzet* → Instaliraj, zatim Opšte → Informacije → **Podešavanja poverenja sertifikata** → uključi za „Caddy Local Authority” |
| macOS | Dvoklik na `ca.crt` → Keychain *System* → otvori sertifikat → Trust → **Always Trust** |

Sertifikaciono telo se čuva u Docker volumenu `caddy_data` i ulazi u noćnu rezervnu kopiju; ako se izgubi, uređaji moraju da instaliraju novi korenski sertifikat.

### 5. Dodavanje Xiaomi kamera

Sve se radi u aplikaciji, kao administrator, na stranici **Kamere** → **Xiaomi kamere**:

1. **Prijava na Xiaomi nalog:** email, telefon ili Xiaomi ID i lozinka. Ako Xiaomi zatraži captcha ili kod poslat porukom, aplikacija prikaže polje za unos. Prijava se čuva u go2rtc-u, a lozinka se nigde ne upisuje.
2. Izaberi nalog i region (Evropa = `de`) i klikni **Učitaj kamere sa naloga**. Prikazuju se sve kamere sa naloga, sa oznakom koje su dostupne iz mreže servera i koje su već dodate.
3. Po želji promeni naziv, uključi **ima motor** za kamere koje se okreću i klikni **Dodaj**. Aplikacija tada sama:
   - pronalazi HD i SD profil slike koji radi (za poznate modele koristi provereni, ostale proba redom);
   - upisuje streamove u go2rtc, sa Opus zvukom koji se pokreće samo za kamere kojima treba;
   - preuzima token kamere sa Xiaomi naloga i čuva ga u `config/miio_tokens.json`;
   - za **modele koje još ne poznaje** čita njihov javni MIoT opis, pravi kontrole (uključi/uspavaj, noćni režim, okretanje slike, žig, WDR, kolor noću, praćenje pokreta, lampica) i zadržava samo one na koje kamera zaista odgovara.

Dodavanje traje 10–60 sekundi. Okretanje (PTZ) se kasnije može uključiti ili isključiti u tabeli kamera.

<details>
<summary>Ručno dodavanje, bez aplikacije (rezerva)</summary>

1. Otvori go2rtc preko SSH tunela (`ssh -L 1984:127.0.0.1:1984 root@<server>`, pa http://localhost:1984, korisnik `admin`, lozinka iz `go2rtc.yaml`).
2. Idi na **Add → Xiaomi**, prijavi se i prepiši `xiaomi://…` adrese kamera u `config/go2rtc.yaml` (vidi `go2rtc.example.yaml`). Za AW200 i C300 dodaj `&subtype=3` (HD), odnosno `&subtype=1` (SD).
3. Tokene preuzmi programom `scripts/xiaomi-tokens` (Go, pokreće se u `golang` kontejneru; uputstvo je u komentaru na početku fajla).
4. `docker compose restart go2rtc && docker compose exec app python -m app.cli import-cameras`

</details>

### 5b. Servis za stanje sistema

Stranica **Sistem** prikazuje procesor i RAM celog servera (CT-a). Te vrednosti mora da meri mali servis koji radi van Docker-a, jer u LXC-u kontejner vidi samo sebe:

```bash
mkdir -p data/fsprobe data/hoststats
install -m 644 scripts/xiaomi-cam-hub-stats.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now xiaomi-cam-hub-stats.service
```

### 6. Noćna rezervna kopija

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

## Snimanje

Snimanjem upravlja poseban servis (`recorder`), koji radi i kada niko nije prijavljen:
1. Video se samo kopira iz go2rtc-a, bez pretvaranja, pa ne opterećuje procesor.
2. Snima se u delove od 10 sekundi, koji se spajaju u klipove do 5 minuta.
3. Klipovi se šalju na deljeni folder: `<kamera>/<datum>/<vreme>_<razlog>.mp4`.
4. Ako je računar sa folderom nedostupan, klipovi čekaju lokalno, do granice podešene u aplikaciji.

**Priprema Windows foldera:**
1. Napravi lokalnog korisnika samo za snimke (npr. `kamere`) sa lozinkom.
2. Na folderu: desni klik → Properties → Sharing → Advanced Sharing → *Share this folder*. U Permissions daj tom korisniku *Full Control*.
3. Računar mora imati stalnu IP adresu i biti uključen dok se snima.

**U aplikaciji** (administrator → **Snimanje**):
1. Unesi IP računara, ime deljenja, korisnika i lozinku, pa klikni **Proveri vezu**. Aplikacija upiše i obriše probni fajl. Lozinka se u bazi čuva šifrovana.
2. Za svaku kameru izaberi način (isključeno, stalno, po rasporedu), raspored (npr. 22:00–06:00, po danima) i broj dana čuvanja.
3. Ručno snimanje pokreće se dugmetom **REC** na kartici kamere, nezavisno od izabranog načina.

Za orijentaciju, stalno snimanje Xiaomi kamere u HD-u zauzima oko 0,6 GB po satu (~14 GB dnevno).

### Snimanje na pokret

Detektor prati SD sliku kamere: 4 sličice u sekundi, smanjene na 160×90 i podeljene na 16×9 polja. Snima se samo kad se pomeri **povezana grupa polja**, dovoljno velika prema podešenoj osetljivosti, i to u dve uzastopne sličice. Čuva se 10 s pre pokreta i 20 s posle poslednjeg pokreta. Server pri tome troši oko 3–4% procesora po kameri.

Svaka dodatna opcija ima svoj prekidač na stranici **Snimanje**, a sve su podrazumevano uključene:

| Opcija | Šta radi |
|---|---|
| Ignoriši delove koji se stalno pomeraju | Delovi slike koji se pomeraju veći deo poslednjih minuta (drveće i žbunje na vetru) privremeno se ne računaju. Čovek koji prođe kroz miran deo slike se računa. Detektoru treba 2–3 minuta da „nauči” scenu. |
| Koristi ručne zone | Klikom na polja preko slike kamere (dugme **Zone**) označiš delove koji se nikad ne gledaju. Uživo se vidi šta se pomera (crveno) i šta je automatski ignorisano (žuto). |
| Prilagodi prag šumu | Prag se sam podiže kad je slika zrnasta (noć, kiša). |
| Traži povezanu grupu | Raštrkana pojedinačna polja (treperenje lišća, šum) ne pokreću snimanje. |
| Snimi i 10 s pre pokreta | Snimak počinje deo pre trenutka kad je pokret uočen. |
| Snimaj zvuk | Važi za sve načine snimanja. |

Promena cele slike odjednom, kao uključenje IR noćnog režima ili svetla, ne računa se kao pokret.

### AI potvrda (osoba, vozilo, životinja)

Po želji, za svaku kameru, pokret se računa tek kada model za prepoznavanje objekata vidi **osobu**, **vozilo** ili **životinju** na mestu gde se nešto pomerilo. Svaka grupa ima svoj prekidač, uz podešavanje minimalne sigurnosti. Model se pokreće samo dok se nešto pomera (~50 ms po slici na procesoru), a obaveštenje kaže šta je viđeno, npr. „Osoba (87%)”. Ako model nije dostupan, snima se svaki pokret kao ranije.

Model (YOLOX-tiny, licenca Apache 2.0) se preuzima jednom:

```bash
mkdir -p /opt/xiaomi-cam-hub/data/models && cd /opt/xiaomi-cam-hub/data/models
curl -fsSLO https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx
echo "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7  yolox_tiny.onnx" | sha256sum -c
chmod 644 yolox_tiny.onnx
```

## Obaveštenja

Podešavaju se kao administrator na stranici **Obaveštenja**. Svaki kanal ima prekidač, dugme **Pošalji probu** (za MQTT **Proveri vezu**) i prikaz poslednjeg uspešnog slanja i greške. Lozinke i tokeni se u bazi čuvaju šifrovani.

| Kanal | Šta treba |
|---|---|
| Telegram | Bot napravljen kod @BotFather (token) i tvoj chat ID. Botu prvo pošalji `/start`. |
| ntfy | ntfy aplikacija na telefonu i tema. Na javnom ntfy.sh izaberi dugačko, teško pogodivo ime teme. |
| Email | SMTP server. Za Gmail: `smtp.gmail.com`, 587, starttls i *lozinka za aplikacije*. |
| Home Assistant | MQTT broker (npr. Mosquitto dodatak). Uz otkrivanje, svaka kamera se pojavi kao uređaj sa senzorima **Pokret** i **Dostupnost**. |

MQTT teme (prefiks se podešava, podrazumevano `xiaomi_cam`):

```text
xiaomi_cam/system/status          online / offline (i „last will”)
xiaomi_cam/<kamera>/status        online / offline
xiaomi_cam/<kamera>/motion        ON / OFF
xiaomi_cam/<kamera>/event         {"camera": "...", "event": "motion", "active": true, "timestamp": "..."}
xiaomi_cam/<kamera>/image         JPEG (pri pokretu i na 10 minuta)
xiaomi_cam/<kamera>/state/<ključ> stanje kontrola i snimanja
xiaomi_cam/<kamera>/set/<ključ>   komande iz HA
xiaomi_cam/<kamera>/ptz           left / right / up / down
```

**Entiteti u Home Assistant-u**, po kameri. Svaka grupa osim prve ima svoj prekidač u MQTT podešavanjima:

| Grupa | Entiteti |
|---|---|
| uvek | Pokret, Dostupnost |
| Slika | `camera` sa poslednjom slikom |
| Kontrole | prekidači i izbori koje kamera podržava (uključena, noćni režim, lampica, okretanje slike, žig, WDR… kod ESP32 rezolucija, kvalitet…) |
| Snimanje | Ručno snimanje (REC), Način snimanja, Snima se, Poslednji pokret |
| Okretanje | dugmad levo/desno/gore/dole (kamere sa motorom). Ako kameru niko ne gleda, aplikacija na kratko otvori stream da bi motor radio. |

Komande iz HA se beleže u audit log kao korisnik `mqtt`. Stanje kontrola se čita iz kamera svakog minuta, pa HA vidi i promene napravljene u aplikaciji ili Mi Home-u.

**Pravila** (svako ima svoj prekidač):
- vrste događaja: pokret, kamera nedostupna, kamera ponovo radi, problem sa snimanjem;
- slika iz kamere uz poruku o pokretu;
- obaveštenja o pokretu posebno za svaku kameru;
- najviše jedna poruka o pokretu iste kamere u zadatom broju minuta;
- tihi sati (bez poruka o pokretu).

Kamera se proglašava nedostupnom tek posle dve uzastopne neuspele provere. Pokret se detektuje samo za kamere sa načinom snimanja „Na pokret”. Home Assistant dobija sve događaje, nezavisno od tihih sati i razmaka između poruka.

## Rad i održavanje

```bash
bash scripts/deploy.sh                     # sa razvojnog računara: prenese kod i ponovo izgradi aplikaciju
docker compose logs -f app                 # log aplikacije
docker compose exec app python -m app.cli create-user <ime> <admin|user|viewer>   # novi korisnik ili nova lozinka
```

Migracije baze (Alembic) pokreću se same pri startu aplikacije.

## Bezbednost

- Aplikacija je namenjena **lokalnoj mreži**. Za pristup spolja koristi VPN (npr. Tailscale). Portove 80/443/8080 nemoj izlagati internetu.
- go2rtc API (1984), RTSP (8554) i baza (5432) slušaju samo na `127.0.0.1`. Na mreži su otvoreni samo aplikacija (8080) i WebRTC video (8555).
- Ne šalji u git i ne deli fajlove `.env`, `config/go2rtc.yaml` (sadrži Xiaomi prijavu) i `config/miio_tokens.json`.
- Uz HTTPS (korak 5a) aplikacija sluša samo lokalno (`APP_HOST=127.0.0.1`), a kolačić sesije ima `Secure` zaštitu (`COOKIE_SECURE=true`).

## Poznata ograničenja

- Okretanje radi samo dok je kamera otvorena na ekranu, jer komanda ide kroz aktivnu video vezu.
- go2rtc odgovara „uspešno” i kada kamera nema motor, pa odgovor ne dokazuje pokret.
- Govor kroz kameru (dugme **🎤 Govori** na stranici kamere, Xiaomi kamere) radi samo preko HTTPS-a sa instaliranim korenskim sertifikatom. Kod Mi 360 (zvuk PCMA) nije potvrđen.
- Ako kameru niko ne gleda, snimak počinje 3–8 sekundi posle pokretanja, dok se ne uspostavi veza sa kamerom.
- Snimci sa Xiaomi kamera su u formatu H.265. Chrome i Edge ih puštaju na većini računara, ali ne na svim. U suprotnom preuzmi snimak i otvori ga u VLC-u.
- Istovremeno gledanje iste Xiaomi kamere kroz Mi Home aplikaciju može izazvati prekide.
- ESP32 `CameraWebServer` primenjuje novu rezoluciju tek na novoj stream vezi. Aplikacija zato ponovo poveže plejer, a drugi gledaoci vide promenu kad osveže stranicu.
- Desilo se da AW200 prestane da odgovara na video vezu. Restart kamere (isključivanje iz struje) je to rešio.

## Licenca

[MIT](LICENSE) © 2026 Zoran Nikolić
