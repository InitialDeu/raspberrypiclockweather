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
| 17 | Se la rete si bloccava (es. Wi-Fi che cade e DNS appeso), il thread di rete poteva restare fermo per sempre: meteo e santo non si aggiornavano più, ma l'orologio continuava a girare e nessuno se ne accorgeva | **Watchdog**: il thread segna un "battito" a ogni ciclo; se manca da più di 3 intervalli (30 min) l'app esce e systemd (`Restart=always`) la riavvia. Il meteo più vecchio di 1 ora viene mostrato come "non disponibile" e il santo di ieri non viene più mostrato oggi; il santo si aggiorna subito dopo mezzanotte | Aggiornamenti che riprendono da soli |
| 18 | La modalità notte (presente fino al commit `a7ea18a`) era sparita e il testo restava giallo pieno anche di notte | **Modalità notte** reintrodotta: dalle 22:30 alle 07:30 il testo passa con dissolvenza di 3 s ad ambra scuro `#5C3300` (più scuro del vecchio `#995500`). Regolabile da `/etc/clockweather.env`: `CW_NIGHT_FG`, `CW_NIGHT_START`, `CW_NIGHT_END`, `CW_NIGHT=off` | Meno luce di notte |
| 19 | Per cambiare città, lingua, colori o rete Wi-Fi bisognava entrare in SSH; un Pi portato in un'altra casa senza conoscerne la password del Wi-Fi restava irraggiungibile | **Console di gestione** in LAN (`admin/`, solo libreria standard) e **hotspot di configurazione** `piclock-setup`: se il Pi non trova reti conosciute crea il suo Wi-Fi, lo schermo mostra nome, password e indirizzo, dal telefono si aggiunge la rete del posto; se la connessione fallisce l'hotspot torna da solo. Dettagli in [ADMIN.md](ADMIN.md) | Pi spostabile senza tastiera/monitor |
| 20 | Il santo poteva restare quello del giorno prima: veniva chiesto "il santo di oggi" subito dopo mezzanotte e la risposta non veniva controllata, ma il sito ha una cache sul server (osservata una copia vecchia di 34 minuti) | (v1.4.0) Il santo viene **richiesto per data** (`?data=AAAA-MM-GG`) e la risposta è accettata solo se **giorno e mese coincidono** (l'anno no: per gennaio 2027 il sito rispondeva `2026-01-01`). Vengono scaricati anche i **7 giorni successivi** e salvati in `~/.cache/clockweather/saints.json`. Nuovo tentativo ogni 60 s finché manca un giorno. Meteo e santo hanno scadenze separate: un errore sul santo non fa più richiedere il meteo ogni minuto | A mezzanotte il santo giusto è già sul Pi, anche senza rete o dopo un riavvio; il santo di un altro giorno non viene mai mostrato |
| 21 | Il meteo era solo testo; nessuna previsione | (v1.4.0) **Icone meteo** disegnate dal programma (`weather_icons.py`, versione giorno/notte, seguono il colore del testo) e una **riga a rotazione** (8 s): min/max delle prossime 24 ore, pioggia o neve nelle prossime 12 (probabilità da 30%), temperatura percepita e umidità, vento, alba e tramonto. La previsione è una seconda chiamata a OpenWeatherMap ogni 30 minuti | Informazioni utili senza immagini né pacchetti in più, leggibili anche di notte. Dalla v1.4.1 vento, umidità e il messaggio "pioggia improbabile" sono stati tolti: vedi punto 26 |
| 22 | La modalità notte seguiva solo orari fissi | `CW_NIGHT=sun`: dal tramonto all'alba, selezionabile anche dalla console | Segue le stagioni |
| 23 | I Pi di solito non hanno un orologio con batteria: senza rete all'avvio ora e data potevano essere sbagliate senza alcun segnale | Avviso **"Ora non sincronizzata"** (da `timedatectl`) finché l'orologio di sistema non è sincronizzato | Si capisce quando non fidarsi |
| 24 | Disposizione pensata per schermi orizzontali; un cambio di risoluzione o rotazione richiedeva il riavvio | Due disposizioni (orizzontale e verticale, con righe spezzate in due), dimensioni calcolate sui font installati e **ricalcolate al volo** se la finestra cambia | Stesso codice sul Pi 4 e sul Pi Zero |
| 25 | Senza rete il meteo scriveva nel log un errore completo ogni minuto, con la chiave API nell'indirizzo | Una riga sola, con la chiave sostituita da `***`; tolto anche lo spazio in "Sant' Umile" | Meno scritture sulla SD, chiave non esposta |
| 26 | L'ora non spiccava abbastanza rispetto alle informazioni | (v1.4.1) Ritorno allo **stile originale** (testo bianco, font tipo Helvetica: Nimbus Sans, data e ora in grassetto). L'ora si prende tutto lo spazio lasciato dalle altre righe. Su schermi 16:10 o più larghi segno, luna e santo stanno su una riga. La **temperatura percepita** è accanto a quella reale, `5°C (perc. 2°)`; la riga a rotazione mostra min/max, alba/tramonto e la pioggia **solo se prevista**. Colore di giorno regolabile con `CW_FG` | È un orologio: l'ora si deve leggere al primo colpo d'occhio |
| 27 | Sullo schermo da 3,5" la parte sotto l'ora era affollata (quattro righe) | (v1.4.2) Sugli schermi piccoli (lato corto sotto 400 px) le righe sono **tre**, più distanziate: meteo, riga a rotazione (dove entrano anche segno e luna) e santo sempre visibile | Più leggibile senza rimpicciolire l'ora |

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

- [x] **Modalità notte**: reintrodotta (punto 18). Nota: lo schermo SPI non ha controllo PWM della retroilluminazione via software, quindi si attenua solo il colore del testo; la retroilluminazione resta accesa.
- [ ] **Emoji della luna**: sono caratteri fuori dal BMP (U+1F311…); con Tk 8.6 e senza font emoji possono comparire come quadratini. Alternativa: simboli BMP (○ ◐ ● ◑) o icone disegnate. Sul Pi 4 (Bookworm, font DejaVu) si vedono correttamente; da controllare sul Pi Zero.
- [x] **Lingua**: italiano opzionale con `CW_LANG=it` (vedi punto 15).
- [x] **Icona meteo**: disegnata dal programma (punto 21).
- [ ] Touch: ripassare pin 21 e 26 se servirà in futuro (poi calibrazione per X con `rotate=270`).
