# Ottimizzazioni e note di installazione

Registro delle modifiche fatte al progetto e della configurazione usata per farlo girare
sul **Raspberry Pi Zero 2 W** con schermo **SPI da 3,5" 480×320** (novembre 2026).

---

## 1. Modifiche al codice (`display_clock_weather.py`, v1.1.0)

| # | Problema nella v1.0.1 | Cosa è stato fatto | Perché |
|---|---|---|---|
| 1 | Font calcolati solo sull'altezza dello schermo: a 480×320 la riga info usciva dai lati | Ogni riga ha una quota dell'altezza utile (`LINE_WEIGHTS`) e il font viene ridotto finché il testo non sta nella larghezza (`set_line`) | Layout corretto a qualunque risoluzione/orientamento (provato a 480×320, 320×480, 1920×1080) |
| 2 | Tutte le info su una sola riga lunghissima | Due righe: meteo (`Città • descrizione • temperatura`) e segno/luna/santo | Più leggibile sugli schermi piccoli, come nello screenshot del README |
| 3 | Se l'API meteo falliva (chiave errata, rete giù) `get_weather()` lanciava un'eccezione: crash all'avvio o loop di aggiornamento interrotto | Errori gestiti, l'ultimo dato valido resta a schermo, altrimenti "weather unavailable"; nuovo tentativo dopo `RETRY_INTERVAL` | L'orologio non deve mai sparire per colpa del meteo |
| 4 | Le richieste HTTP (meteo, santo) giravano nel thread di Tkinter: con rete lenta l'interfaccia si bloccava fino a 5 s + 5 s | Rete spostata in un thread in background (`network_loop`), la UI legge uno stato condiviso protetto da lock | UI sempre reattiva |
| 5 | Meteo richiesto ogni 60 s, `UPDATE_INTERVAL = 600` dichiarato ma mai usato | Meteo ogni `WEATHER_INTERVAL` (600 s) | 144 chiamate/giorno invece di 1440 |
| 6 | Aggiornamento ogni 60 s a partire dall'avvio: l'ora poteva restare indietro fino a 59 s | `tick()` si riprogramma esattamente all'inizio del minuto successivo | L'ora cambia allo scoccare del minuto |
| 7 | Cache del santo e della luna condividevano `cached_day`; se il fetch del santo falliva, veniva riprovato ogni minuto | Cache separata (`saint_day`), santo scaricato una volta al giorno (con retry se fallisce) | Meno richieste, logica più chiara |
| 8 | Fase lunare arrotondata per difetto (`int(x*8)`): ogni fase "iniziava" nel momento esatto invece di esserne centrata; data di riferimento senza ora | Arrotondamento al più vicino (`int(x*8 + 0.5) % 8`), luna nuova di riferimento 2000-01-06 18:14 UTC, calcolo in UTC | Fase mostrata più fedele |
| 9 | `from PIL import Image, ImageTk` (mai usati) → su Raspberry Pi OS serve il pacchetto extra `python3-pil.imagetk`, altrimenti crash all'avvio | Import inutilizzati rimossi (`PIL`, `BytesIO`, `time`) | Una dipendenza in meno, niente crash `ImportError` |
| 10 | Log in `/var/log/clockweather.log`: se il file non esiste/non è scrivibile dall'utente `pi`, crash all'avvio | Fallback automatico su `~/clockweather.log` | Parte comunque |
| 11 | Chiave API scritta nello script (rischio di pubblicarla su GitHub) | `API_KEY` e `CITY` leggibili da variabili d'ambiente (`OWM_API_KEY`, `CW_CITY`), caricate dal service da `/etc/clockweather.env` | La chiave non sta più nel repo |
| 12 | Finestra senza dimensioni esplicite: senza window manager restava 200×200 | `root.geometry(f"{W}x{H}+0+0")` oltre a `-fullscreen`; cursore del mouse nascosto | Funziona anche in sessioni X minimali / kiosk |
| 13 | Parametri della richiesta meteo concatenati nell'URL | `params=` di `requests` (encoding corretto di città con spazi/accenti) | Robustezza |
| 14 | `datetime.utcnow()` deprecato nelle versioni recenti di Python | `datetime.now(timezone.utc)` | Compatibilità futura |
| 15 | La versione italiana (quella dello screenshot `ClockExample.png`) non era più nel codice | **Italiano opzionale**: `CW_LANG=it` (default `en`). Testi in un dizionario `TEXTS`: segno zodiacale e fasi lunari in italiano, data `gg/mm/aaaa`, messaggi di errore, e descrizione meteo in italiano da OpenWeatherMap | Una sola base di codice per le due lingue |
| 16 | Il santo veniva mostrato come `Saint Santa Cecilia`: l'API di santodelgiorno.it restituisce già il titolo nel campo `nome` | In italiano il nome resta com'è (`Santa Cecilia`, `Sant'Aldo`); in inglese il titolo viene tradotto (`St. Cecilia`, `Sts. ...`, `Blessed ...`) | Niente titolo doppio |

### Service systemd (`clockweather.service`)

- Percorsi corretti: il file nel repo puntava a `/repository` e `/usr/src/scripts`, il README a `/opt/raspberrypiclockweather`. Ora è tutto su `/opt/raspberrypiclockweather`.
- `After=graphical.target` + `RestartSec=10`: se X non è ancora pronto il service riprova ogni 10 s invece di esaurire i tentativi in un secondo.
- Rimosso `XDG_RUNTIME_DIR=/run/user/%U`: in un'unità di sistema `%U` può risolversi nell'UID di root, e Tkinter non ne ha bisogno.
- `EnvironmentFile=-/etc/clockweather.env` (opzionale) per chiave API e città.

### README

- Dipendenze aggiornate (non serve più `python3-pil`).
- Aggiunta la creazione del file di log con owner `pi`.
- Configurazione della chiave tramite `/etc/clockweather.env`.
- Link a `docs/REQUIREMENTS.md` (requisiti diversi tra Bookworm/HDMI e Bullseye/SPI).

### `.gitignore` (nuovo)

- Esclude `__pycache__/`, `*.pyc`, `*.log` e `.env`, così file compilati, log e chiavi non finiscono nel repo.

---

## 2. Setup dello schermo SPI 3,5" (Pi Zero 2 W, Raspberry Pi OS Bullseye 32 bit)

**Hardware**: "3.5inch RPi Display" 480×320 (clone MPI3501 / Waveshare 3.5" A).
Controller ILI9486 pilotato tramite registri a scorrimento 74HC4094 (bus a 16 bit), touch resistivo XPT2046.
Pin: DC = GPIO24, RESET = GPIO25, LCD su SPI0 CE0, touch su SPI0 CE1, IRQ touch = GPIO17.

### Cosa NON fare
- **Non usare gli script `LCD-show`** (goodtft/waveshare): riscrivono `config.txt`, `cmdline.txt` e la configurazione di X e su Bullseye/Bookworm spesso rompono il sistema.
- `dtoverlay=hy28a` (era nel config precedente) è un altro schermo: risultato schermo bianco.
- `dtoverlay=piscreen` (stesso controller, presente nel kernel) carica il driver ma usa la sequenza di init generica dell'ILI9486: su questo clone lo schermo resta bianco.

### Configurazione funzionante
1. Overlay `tft35a` preso dal repo goodtft (solo il file, non lo script):
   ```bash
   wget -O /tmp/tft35a.dtbo https://github.com/goodtft/LCD-show/raw/master/usr/tft35a-overlay.dtb
   cp /tmp/tft35a.dtbo /boot/overlays/tft35a.dtbo
   ```
2. In `/boot/config.txt` (driver video legacy, **senza** `dtoverlay=vc4-kms-v3d`):
   ```
   dtparam=spi=on
   dtoverlay=tft35a:rotate=270
   ```
   `rotate=90` / `270` = orizzontale 480×320 (270 = capovolto, cavo dal lato opposto); `0` / `180` = verticale 320×480.
   Il codice si adatta da solo all'orientamento.
3. X sul framebuffer dello schermo (`/dev/fb1`) invece che sull'HDMI (`/dev/fb0`):
   in `/usr/share/X11/xorg.conf.d/99-fbturbo.conf` → `Option "fbdev" "/dev/fb1"`.
4. Utente `pi` (senza password) con autologin nella sessione grafica, per il service:
   ```bash
   adduser --disabled-password --gecos "" pi
   usermod -aG video,input,render,audio,gpio,spi,i2c,plugdev,netdev,users pi
   sed -i 's/^\s*autologin-user=.*/autologin-user=pi/' /etc/lightdm/lightdm.conf
   raspi-config nonint do_blanking 1     # niente spegnimento schermo dopo 10 min
   ```
5. Backup dei file originali sul Pi: `/boot/config.txt.bak-2026-11-22`, `/boot/cmdline.txt.bak-2026-11-22`.

### Test utili
```bash
dmesg | grep -iE 'graphics fb1|ads7846'           # deve comparire "fb_ili9486 frame buffer, 480x320"
cat /dev/urandom > /dev/fb1                         # rumore colorato = pannello OK ("Spazio esaurito" è normale)
cat /dev/zero > /dev/fb1                            # schermo nero
```

### Diagnosi hardware (lezione imparata)
Schermo **bianco fisso** con driver caricato correttamente = il pannello non riceve dati.
Nel nostro caso erano **saldature fredde** sul pettine GPIO del Pi Zero (saldature "a ghiacciolo").
Indizio decisivo: anche il touch, che condivide MOSI/MISO/SCLK, generava interrupt su GPIO17 ma nessuna coordinata.
Rifatte le saldature con flussante, lo schermo funziona.

Il **touch** ancora non restituisce coordinate (interrupt OK, dati no): restano da ripassare
**pin 21 (MISO)** e **pin 26 (CE1)**. Per l'orologio non serve.

---

## 3. Da fare / idee

- [ ] **Modalità notte**: il README la cita (22:30–07:30, transizioni di colore) ma il codice attuale non la implementa: è stata rimossa nel commit `bfd5e89`. L'implementazione precedente (`adjust_night_colors`, `smooth_color_transition`) è ancora nella history, commit `a7ea18a` "Updated the Night Fade": si può riprendere da lì. Nota: questo schermo non ha controllo PWM della retroilluminazione via software, quindi solo colori attenuati.
- [ ] **Emoji della luna**: sono caratteri fuori dal BMP (U+1F311…); con Tk 8.6 e senza font emoji possono comparire come quadratini. Alternativa: simboli BMP (○ ◐ ● ◑) o icone PNG.
- [x] **Lingua**: italiano opzionale con `CW_LANG=it` (vedi punto 15).
- [ ] **Icona meteo**: l'API restituisce il codice icona (`03d` ecc.) che oggi non viene usato.
- [ ] Touch: ripassare pin 21 e 26 se servirà in futuro (poi calibrazione per X con `rotate=270`).
