# RSS Deluge Bot

A Python application for automatically monitoring an RSS feed and adding matching torrents to Deluge WebUI.

The project consists of two independent components:

- **RSS Bot** – monitors RSS feeds, filters releases, adds torrents to Deluge, renames completed downloads, and optionally queues files for transcoding.
- **Transcode Worker** – processes queued files, performs optional HEVC transcoding using FFmpeg, preserves metadata, and reports progress through Discord webhooks.

Both components share the same configuration file.

> Use only with content you are legally allowed to download and process.

---

# ✨ Features

- RSS torrent feed support
- Nyaa RSS support
- Deluge WebUI integration
- SQLite duplicate protection
- Per-series configuration
- Python Regex filtering
- Automatic download folder selection
- Automatic file renaming
- Discord notifications
- Optional HEVC transcoding (FFmpeg + FFprobe)
- Shared configuration for all components
- Example systemd services
- Dry-run mode

---

# 🚀 Quick Start

Clone the repository:

```bash
git clone https://github.com/Wardf1/NyaaRSS-Deluge-Torrent-bot.git
cd NyaaRSS-Deluge-Torrent-bot
```

Install dependencies:

```bash
python3 -m pip install requests
```

Copy the example configuration:

```bash
cp bot/config.example.json bot/config.json
```

Edit the configuration:

```bash
nano bot/config.json
```

Run the RSS bot:

```bash
cd bot
python3 bot.py
```

Run the transcode worker:

```bash
python3 transcode_worker.py
```

For complete installation, configuration, Deluge setup, transcoding, Discord webhook configuration, Regex examples, database information and systemd installation, see the language-specific documentation linked above.

---

# 📁 Project Structure

```text
bot/
├── bot.py
├── transcode_worker.py
├── regex_gen.py
└── config.example.json

systemd/
├── nyaa-rss-deluge-bot.service
├── nyaa-rss-deluge-bot.timer
└── nyaa-transcode-worker.service
```

---

# 📄 File Overview

## `bot.py`

Main RSS and Deluge integration service.

The script downloads the configured RSS feed, parses torrent metadata, applies global and per-series filters, prevents duplicate downloads, adds matching torrents to Deluge, tracks downloaded files, performs automatic renaming, and optionally queues completed media for transcoding. :contentReference[oaicite:0]{index=0} :contentReference[oaicite:1]{index=1}

Features:

- Downloads and parses RSS feeds
- Supports Nyaa-specific metadata, including:
  - info hash
  - category
  - size
  - seeder and leecher count
  - trusted status
  - remake status
- Applies global filters
- Applies per-series filters
- Supports `include_regex` and `exclude_regex`
- Supports per-series:
  - download locations
  - minimum seeder requirements
  - trusted-only rules
  - remake filtering
  - rename templates
  - transcoding settings
- Connects to Deluge WebUI and automatically connects it to a configured daemon
- Downloads `.torrent` files and adds them through the Deluge API
- Handles missing `.torrent` files without stopping the entire RSS run
- Prevents duplicate downloads using `seen.sqlite3`
- Stores pending rename information in SQLite
- Renames media files after the torrent is no longer present in Deluge
- Supports `.mkv`, `.mp4`, `.avi`, and `.m4v`
- Adds completed media to `transcode_queue.json` when transcoding is enabled
- Uses file locking to protect the shared transcode queue
- Sends Discord notifications for:
  - added torrents
  - bot errors
  - periodic systemd journal reports
- Supports dry-run mode
- Continues RSS processing when filesystem or queue preparation fails :contentReference[oaicite:2]{index=2} :contentReference[oaicite:3]{index=3} :contentReference[oaicite:4]{index=4}

### Typical Workflow

1. The bot connects to Deluge WebUI and its daemon.
2. It checks pending entries in `seen.sqlite3`.
3. While a torrent is still present in Deluge, the bot reads the actual media path reported by `files[].path` and saves a rename plan.
4. After the torrent is removed from Deluge, the bot:
   - renames the downloaded media file,
   - adds it to the transcode queue when enabled,
   - marks the database entry as completed.
5. The bot downloads the RSS feed.
6. Every RSS item is checked against:
   - duplicate protection,
   - global filters,
   - per-series rules.
7. Matching torrents are downloaded and added to Deluge.
8. The bot sends a Discord notification and stores the torrent in SQLite.
9. Periodically, systemd journal logs can be sent to Discord. :contentReference[oaicite:5]{index=5} :contentReference[oaicite:6]{index=6}

### Duplicate Protection

Every accepted torrent is stored in:

```text
seen.sqlite3
```

The database uses the torrent info hash, GUID, or torrent URL as the unique identifier.

This prevents the same torrent from being added again after:

- a bot restart,
- a Deluge restart,
- removing the torrent from Deluge,
- moving or renaming the downloaded file. :contentReference[oaicite:7]{index=7}

### Automatic File Renaming

Rename rules are configured per series with `set_name`.

Example:

```json
"set_name": "[ToonsHub] Example Series - {se}"
```

For an RSS release containing `S01E05`, the final filename can become:

```text
[ToonsHub] Example Series - S01E05.mkv
```

The bot uses the actual path reported by Deluge instead of assuming that the RSS title and downloaded filename are identical. :contentReference[oaicite:8]{index=8}

### Transcode Queue Integration

After a successful filesystem rename, the bot can add the media file to:

```text
transcode_queue.json
```

A queue job contains:

- torrent ID
- original RSS title
- matched series pattern
- final media path
- job status
- attempt count
- queue timestamp

Files are only queued when:

- global transcoding is enabled,
- the matched series has `transcode: true`,
- the extension is supported. :contentReference[oaicite:9]{index=9}

### Discord Notifications

The bot supports:

- `discord_webhook` — notification after adding a torrent,
- `discord_journal_webhook` — periodic systemd journal uploads,
- error notifications when the main process fails. :contentReference[oaicite:10]{index=10} :contentReference[oaicite:11]{index=11}

### Running

Run once:

```bash
python3 bot.py
```

The intended production setup is a `systemd` timer that starts the bot at regular intervals.

---

## `transcode_worker.py`

Background worker responsible for processing files queued by the RSS bot.

The worker continuously monitors the transcode queue, analyzes downloaded media using FFprobe, and performs optional HEVC transcoding with FFmpeg. It is designed to run independently from the RSS bot, allowing downloads and media processing to occur in parallel. :contentReference[oaicite:0]{index=0}

Features:

- Reads jobs from `transcode_queue.json`
- Safe queue handling with file locking
- Supports one-shot mode or continuous daemon mode
- Detects the actual video codec using FFprobe
- Automatically skips files already encoded in HEVC
- Optional metadata-only remux when stream titles need updating
- Supports hardware-accelerated encoding:
  - VAAPI
  - NVENC
  - Intel Quick Sync (QSV)
  - Software encoding (`libx265`)
- Preserves:
  - audio tracks
  - subtitle tracks
  - attachments
  - chapters
  - container metadata
- Verifies the output after processing:
  - video codec
  - duration
  - audio track count
  - subtitle track count
- Automatically renames the finished file to include its detected codec
  (e.g. `[HEVC]`, `[H.264]`, `[AV1]`)
- Supports configurable stream titles for video, audio and subtitles
- Sends Discord notifications when:
  - transcoding starts
  - transcoding completes
  - transcoding fails
  - a file is skipped because no conversion is required
- Automatically recovers stale processing jobs after a configurable timeout
- Shared configuration with the RSS bot (`config.json`) :contentReference[oaicite:1]{index=1} :contentReference[oaicite:2]{index=2}

### Typical Workflow

1. The RSS bot downloads a torrent.
2. After the download finishes, the RSS bot adds a job to `transcode_queue.json`.
3. The worker claims the next queued job.
4. FFprobe analyzes the source file.
5. The worker decides whether to:
   - skip the file,
   - perform a metadata-only remux,
   - transcode the video to HEVC.
6. FFmpeg processes the media when required.
7. The output is verified to ensure no streams were lost.
8. The finished file replaces the original (if configured).
9. The filename is updated to include the detected codec.
10. A Discord notification is sent with the result. :contentReference[oaicite:3]{index=3}

### Running

Process a single queued job:

```bash
python3 transcode_worker.py
```

Process all queued jobs and exit:

```bash
python3 transcode_worker.py --all
```

Run continuously as a daemon:

```bash
python3 transcode_worker.py --daemon
```

Run as a daemon with a custom polling interval:

```bash
python3 transcode_worker.py --daemon --interval 30
```

The worker is intended to be started as a `systemd` service, where it continuously watches the queue and processes new jobs as they become available. :contentReference[oaicite:4]{index=4}

---

## `regex_gen.py`

Command-line utility for automatically generating `series` entries for `config.json` from release names stored in a TXT file.

The generator analyses release names and creates ready-to-use configuration entries, eliminating most manual work when adding new series.

Features:

- Reads one release per line from a TXT file
- Detects release groups (e.g. ToonsHub, Judas, ASW, VARYG, DKB, SubsPlease)
- Detects season and episode numbers
- Supports multiple episode formats (`S01E01`, `1x01`, `- 01`)
- Extracts English and alternative (Japanese/Romaji) titles
- Automatically groups different names of the same series
- Detects:
  - video codec
  - source platform
  - resolution
  - subtitle type
- Automatically decides whether `transcode` should be enabled
- Generates optimized Python-compatible `include_regex`
- Produces ready-to-use JSON that can be copied directly into `config.json`

Example:

Input (`series.txt`):

```text
[ToonsHub] Daemons of the Shadow Realm S01E17 1080p CR WEB-DL AAC2.0 H.264 (Yomi no Tsugai, Multi-Subs)
[Judas] BLEACH Thousand-Year Blood War - S01E42 [1080p][HEVC x265 10bit][Multi-Subs]
```

Run:

```bash
python3 regex_gen.py series.txt
```

Output:

```text
generated_series.json
```

The generated file contains a complete `series` configuration with:

- merged title aliases,
- generated `include_regex`,
- automatically selected `transcode` option,
- default rule values ready to paste into `config.json`.

For advanced Regex examples and customization, see the dedicated Regex documentation.

---

## `config.example.json`

Example configuration shared by both `bot.py` and `transcode_worker.py`.

The configuration file controls every aspect of the application, including RSS sources, Deluge connectivity, filtering rules, Discord notifications, and transcoding options.

Features:

- RSS feed configuration
- Deluge WebUI connection settings
- Global filtering options
- Default download location
- Per-series configuration
- Python Regular Expression filters
- Automatic file renaming rules
- Per-series transcoding control
- Discord webhook configuration
- Journal reporting settings
- FFmpeg / FFprobe configuration
- Hardware acceleration settings
- Stream title customization
- Audio and subtitle language mapping
- Queue and transcoding behavior

Each series can define its own:

- `include_regex`
- `exclude_regex`
- `trusted_only`
- `skip_remakes`
- `min_seeders`
- `download_location`
- `set_name`
- `default_season`
- `transcode`

The transcode section controls:

- FFmpeg and FFprobe paths
- Hardware encoder selection
- Video quality settings
- Audio, subtitle and attachment handling
- Output verification
- Metadata and stream title handling
- Queue management
- Automatic cleanup after successful transcoding

The repository includes only `config.example.json`. Create your own `config.json` and never publish personal information such as passwords, webhook URLs or download paths.

---



---

# 📄 License

This project is licensed under the MIT License.
