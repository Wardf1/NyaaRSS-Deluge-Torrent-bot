# RSS Deluge Bot

Prosty bot w Pythonie do automatycznego dodawania torrentów z kanału RSS do Deluge WebUI.

Bot odczytuje kanał RSS, filtruje tytuły torrentów przy użyciu konfigurowalnych reguł, zapobiega duplikatom za pomocą SQLite i wysyła pasujące torrenty do Deluge przez WebUI API.

> Używaj wyłącznie z treściami, które masz prawo legalnie pobierać.

## I. Funkcje

* Obsługa torrentowych kanałów RSS
* Integracja z Deluge WebUI
* Ochrona przed duplikatami (`seen.sqlite3`)
* Filtrowanie osobno dla każdej serii
* Opcjonalna domyślna lokalizacja pobierania
* Osobne lokalizacje pobierania dla konkretnych serii
* Dopasowywanie tytułów za pomocą Regex
* Tryb testowy dry-run
* Konfigurowalne filtry grup wydających
* Obsługa RSS z Nyaa

---

## II. Wymagania

* Python 3.8+
* Deluge z włączonym WebUI

Instalacja zależności:

```bash
pip install requests
```

---

## III. Instalacja

Sklonuj repozytorium:

```bash
git clone https://github.com/YOUR_USERNAME/rss-deluge-bot.git
cd rss-deluge-bot
```

Zainstaluj zależności:

```bash
pip install requests
```

Skopiuj przykładową konfigurację:

```bash
cp config.example.json config.json
```

Edytuj plik konfiguracyjny:

```bash
nano config.json
```

---

## IV. Konfiguracja

Pełny przykład konfiguracji jest dostępny tutaj:

* [config.example.json](https://github.com/Wardf1/NyaaRSS-Deluge-Torrent-bot/blob/main/bot/config.example.json)

Zmień następujące wartości:

* `deluge_url`
* `deluge_password`

oraz dostosuj listę serii do własnych potrzeb.

---

## V. Lokalizacje pobierania

Bot obsługuje trzy tryby ustawiania folderu pobierania.

### 1. Folder przypisany do konkretnej serii

Jeśli dana seria zawiera:

```json
"download_location": "/downloads/Anime/Dr Stone"
```

ten folder zawsze zostanie użyty dla tej serii.

---

### 2. Globalny domyślny folder

Jeśli seria nie ma własnej lokalizacji pobierania, bot użyje wartości:

```json
"default_download_location": "/downloads/Anime"
```

---

### 3. Domyślny folder Deluge

Jeśli `default_download_location` zostanie całkowicie pominięte, bot nie wyśle ścieżki pobierania.

W takim przypadku Deluge użyje własnego domyślnego folderu pobierania.

---

## VI. Wyrażenia regularne Regex

Pełna lista Regex znajduje się tutaj: [Regex List](https://github.com/Wardf1/NyaaRSS-Deluge-Torrent-bot/blob/main/Extras/REGEX.md)

Regex możesz testować tutaj, ustawiając Flavor na Python: [Regex Tester](https://regex101.com/)

### 1. Regex dla nazw serii

Nazwy serii są dopasowywane za pomocą wyrażeń regularnych Pythona.

Przykład:

```json
"Dr\\.? ?Stone|Science Future"
```

dopasuje wszystkie poniższe tytuły:

```txt
Dr Stone
Dr.Stone
Dr. Stone
Science Future
```

Inny przykład:

```json
"One Piece"
```

dopasuje każdy tytuł zawierający:

```txt
One Piece
```

---

### 2. Regex dla grup wydających

Możesz ograniczyć pobieranie tylko do konkretnych grup wydających.

Przykład:

```json
"include_regex": [
    "^\\[SubsPlease\\].*1080p"
]
```

Dopasuje:

```txt
[SubsPlease] One Piece - 1135 (1080p)
```

ale pominie:

```txt
[Erai-raws] One Piece...
[Judas] One Piece...
```

---

### 3. Wiele grup wydających

```json
"include_regex": [
    "^\\[SubsPlease\\].*1080p",
    "^\\[Judas\\].*1080p",
    "^\\[Erai-raws\\].*1080p"
]
```

Torrent pasujący do któregokolwiek z tych wyrażeń zostanie zaakceptowany.

---

### 4. Wykluczanie wydań

Przykład:

```json
"exclude_regex": [
    "480p",
    "720p",
    "Batch"
]
```

To odrzuci wydania zawierające:

* 480p
* 720p
* Batch

---

## VII. Ochrona przed duplikatami

Bot zapisuje każdy poprawnie dodany torrent w pliku:

```txt
seen.sqlite3
```

używając **infoHash** torrenta.

Dzięki temu bot **nie pobierze tego samego torrenta drugi raz**, nawet jeśli:

* pliki zostały przeniesione,
* torrent został usunięty z Deluge,
* bot został uruchomiony ponownie.

Torrent zostanie dodany ponownie tylko wtedy, gdy:

* plik `seen.sqlite3` zostanie usunięty,
* wpis w bazie danych zostanie ręcznie usunięty,
* uploader opublikuje nowy torrent z innym infoHash.

---

## VIII. Uruchamianie

Tryb testowy:

```bash
python3 bot.py
```

Gdy wszystko wygląda poprawnie, ustaw:

```json
"dry_run": false
```

Następnie uruchom ponownie:

```bash
python3 bot.py
```

---

## IX. Cron

Uruchamianie co 10 minut:

```cron
*/10 * * * * cd /path/to/rss-deluge-bot && /usr/bin/python3 bot.py >> bot.log 2>&1
```

---

## X. Licencja

MIT License.
