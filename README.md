# RSS Deluge Bot

Simple Python bot for automatically adding torrents from an RSS feed to Deluge WebUI.

The bot reads an RSS feed, filters torrent titles using configurable rules, prevents duplicates using SQLite, and sends matching torrents to Deluge through its WebUI API.

> Use only with content you are legally allowed to download.

## Features

* RSS torrent feed support
* Deluge WebUI integration
* Duplicate protection (`seen.sqlite3`)
* Per-series filtering
* Optional default download location
* Per-series download locations
* Regex-based title matching
* Dry-run mode
* Configurable release group filters
* Nyaa RSS support

---

## Requirements

* Python 3.8+
* Deluge with WebUI enabled

Install dependencies:

```bash
pip install requests
```

---

## Installation

Clone the repository:

```bash
git clone https://github.com/YOUR_USERNAME/rss-deluge-bot.git
cd rss-deluge-bot
```

Install dependencies:

```bash
pip install requests
```

Copy the example configuration:

```bash
cp config.example.json config.json
```

Edit it:

```bash
nano config.json
```

---

## Configuration

A complete example configuration is available here:

- [config.example.json](https://github.com/Wardf1/NyaaRSS-Deluge-Torrent-bot/blob/main/bot/config.example.json)
  
Replace the following values:

* `deluge_url`
* `deluge_password`

and adjust the series list to your needs.

---

## Download Locations

The bot supports three download modes.

### 1. Series-specific folder

If a series contains:

```json
"download_location": "/downloads/Anime/Dr Stone"
```

that folder will always be used.

---

### 2. Global default folder

If the series does **not** specify a download location, the bot falls back to:

```json
"default_download_location": "/downloads/Anime"
```

---

### 3. Deluge default folder

If `default_download_location` is omitted entirely, the bot does **not** send a download path.

Deluge will then use its own configured default download directory.

---

## Regular Expressions (Regex)

Series names are matched using Python regular expressions.

Example:

```json
"Dr\\.? ?Stone|Science Future"
```

matches all of the following:

```
Dr Stone
Dr.Stone
Dr. Stone
Science Future
```

Another example:

```json
"One Piece"
```

matches any title containing:

```
One Piece
```

Here is a full Regex list: [Regex List](https://github.com/Wardf1/NyaaRSS-Deluge-Torrent-bot/blob/main/REGEX.md) ..
You can test your Regex here(Change Flavor to Python): [Regex Tester](https://regex101.com/)

---

### Release Groups

You can restrict downloads to specific release groups.

Example:

```json
"include_regex": [
    "^\\[SubsPlease\\].*1080p"
]
```

Matches:

```
[SubsPlease] One Piece - 1135 (1080p)
```

but ignores:

```
[Erai-raws] One Piece...
[Judas] One Piece...
```

---

### Multiple Release Groups

```json
"include_regex": [
    "^\\[SubsPlease\\].*1080p",
    "^\\[Judas\\].*1080p",
    "^\\[Erai-raws\\].*1080p"
]
```

A torrent matching **any** of these expressions is accepted.

---

### Excluding Releases

Example:

```json
"exclude_regex": [
    "480p",
    "720p",
    "Batch"
]
```

This rejects releases containing:

* 480p
* 720p
* Batch

---

## Duplicate Protection

The bot stores every successfully added torrent inside:

```
seen.sqlite3
```

using the torrent's **infoHash**.

This means it **will not** download the same torrent twice, even if:

* the files were moved,
* the torrent was removed from Deluge,
* the bot was restarted.

A torrent will only be added again if:

* `seen.sqlite3` is deleted,
* the database entry is removed,
* the uploader publishes a new torrent with a different infoHash.

---

## Running

Test mode:

```bash
python3 bot.py
```

When everything looks correct:

```json
"dry_run": false
```

Run again:

```bash
python3 bot.py
```

---

## Cron

Run every 10 minutes:

```cron
*/10 * * * * cd /path/to/rss-deluge-bot && /usr/bin/python3 bot.py >> bot.log 2>&1
```

---

## License

MIT License.
