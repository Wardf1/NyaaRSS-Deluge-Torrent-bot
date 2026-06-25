# RSS Deluge Bot

Simple Python bot for automatically adding torrents from an RSS feed to Deluge WebUI.

The bot reads RSS items, applies configurable filters, prevents duplicates using a local SQLite database, and sends matching torrent URLs to Deluge.

> Use only with content you are legally allowed to download.

## Features

* RSS torrent feed support
* Deluge WebUI JSON API support
* Duplicate protection using `seen.sqlite3`
* Regex-based series matching
* Per-series filters
* Optional default download location
* Optional Deluge default download location
* Dry-run mode for safe testing
* Support for Nyaa RSS fields:

  * `infoHash`
  * `seeders`
  * `trusted`
  * `remake`
  * `category`
  * `size`

## Requirements

* Python 3.8+
* Deluge with WebUI enabled
* Python package:

```bash
pip install requests
```

## Files

Recommended project structure:

```text
torrent_bot/
├── bot.py
├── config.json
├── config.example.json
├── seen.sqlite3
└── README.md
```

`seen.sqlite3` is created automatically after the first run.

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

Copy the example config:

```bash
cp config.example.json config.json
```

Edit the config:

```bash
nano config.json
```

## Example config

```json
{
  "rss_url": "https://nyaa.si/?page=rss",
  "deluge_url": "http://127.0.0.1:8112",
  "deluge_password": "CHANGE_ME",

  "dry_run": true,

  "default_download_location": "/mnt/hdd16tb/samba/download/",
  "only_configured_series": true,

  "torrent_options": {
    "add_paused": false
  },

  "trusted_only": false,
  "skip_remakes": false,
  "min_seeders": 0,

  "include_regex": [],
  "exclude_regex": [
    "480p",
    "720p"
  ],

  "series": {
    "Dr\\.? ?Stone|Science Future": {
      "include_regex": [
        "^\\[Judas\\].*1080p.*HEVC"
      ],
      "trusted_only": false,
      "skip_remakes": false,
      "min_seeders": 0
    },

    "Himekishi wa Barbaroi no Yome|The Warrior Princess and the Barbaric King": {
      "include_regex": [
        "^\\[Judas\\].*1080p.*HEVC"
      ],
      "trusted_only": false,
      "skip_remakes": false,
      "min_seeders": 0
    }
  }
}
```

## Configuration explained

### `rss_url`

RSS feed URL.

```json
"rss_url": "https://nyaa.si/?page=rss"
```

### `deluge_url`

Deluge WebUI address.

```json
"deluge_url": "http://127.0.0.1:8112"
```

### `deluge_password`

Password for Deluge WebUI.

```json
"deluge_password": "CHANGE_ME"
```

### `dry_run`

If enabled, the bot only prints what it would add.

```json
"dry_run": true
```

Set to `false` when you are ready to actually add torrents.

### `default_download_location`

Fallback download path used when a matched series does not define its own `download_location`.

```json
"default_download_location": "/mnt/hdd16tb/samba/download/"
```

If you remove this option, the bot will not send `download_location` to Deluge. Deluge will then use its own default download directory.

### `only_configured_series`

If enabled, the bot only downloads torrents matching one of the configured series.

```json
"only_configured_series": true
```

This is recommended.

### `torrent_options`

Options passed directly to Deluge.

```json
"torrent_options": {
  "add_paused": false
}
```

### Global filters

```json
"trusted_only": false,
"skip_remakes": false,
"min_seeders": 0
```

These apply to all torrents before series-specific filters.

### Global include/exclude regex

```json
"include_regex": [],
"exclude_regex": [
  "480p",
  "720p"
]
```

If `include_regex` is empty, no global include filter is applied.

### Series rules

Each key inside `series` is a regex matched against the torrent title.

```json
"Dr\\.? ?Stone|Science Future": {
  "include_regex": [
    "^\\[Judas\\].*1080p.*HEVC"
  ]
}
```

This matches titles containing:

* `Dr Stone`
* `Dr.Stone`
* `Science Future`

and only accepts Judas 1080p HEVC releases.

## Per-series download path

By default, a series uses `default_download_location`.

To use a separate folder for a series:

```json
"Dr\\.? ?Stone|Science Future": {
  "download_location": "/mnt/hdd16tb/samba/download/Dr Stone",
  "include_regex": [
    "^\\[Judas\\].*1080p.*HEVC"
  ]
}
```

If `download_location` is omitted, the bot falls back to `default_download_location`.

If both `download_location` and `default_download_location` are missing, Deluge uses its own configured default folder.

## Duplicate protection

The bot stores added torrents in:

```text
seen.sqlite3
```

It uses the torrent `infoHash` as the main ID.

This means the bot will not add the same torrent again even if:

* the downloaded files are moved,
* the torrent is removed from Deluge,
* the bot is restarted.

The bot may add a torrent again only if:

* `seen.sqlite3` is deleted,
* the database entry is manually removed,
* the uploader releases a new torrent with a different info hash.

## Running

Test first:

```bash
python3 bot.py
```

When everything looks correct, edit `config.json`:

```json
"dry_run": false
```

Then run again:

```bash
python3 bot.py
```

## Cron example

Run every 10 minutes:

```bash
crontab -e
```

Add:

```cron
*/10 * * * * cd /mnt/sda/bots/torrent_bot && /usr/bin/python3 bot.py >> bot.log 2>&1
```

## Example log

```text
INFO: Fetching RSS...
INFO: Found 75 RSS item(s).
INFO: Matched: [Judas] Dr Stone - Science Future - S04E37 [1080p][HEVC x265 10bit]
INFO: Path: /mnt/hdd16tb/samba/download/
INFO: Added to Deluge.
```

## License

MIT License.
