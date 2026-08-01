# RSS Deluge Bot

A Python bot for automatically monitoring an RSS feed and adding matching torrents to Deluge WebUI.

The bot supports configurable filters, per-series rules, duplicate protection using SQLite, automatic file renaming, Discord notifications, and optional HEVC transcoding through a separate worker.

> Use only with content you are legally allowed to download and process.

---

## 📖 Documentation

Choose your preferred language:

| Language          | Documentation                                            |
| ----------------- | -------------------------------------------------------- |
| 🇺🇸 English (US) | [README (En-us).md](README-Lang/README%20%28En-us%29.md) |
| 🇵🇱 Polski       | [README-(PL-pl).md](README-Lang/README-%28PL-pl%29.md)   |

Additional translations may be added in future releases.

---

## ✨ Features

* RSS torrent feed support
* Nyaa RSS support
* Deluge WebUI integration
* Duplicate protection using SQLite
* Per-series filtering
* Regex-based title matching
* Configurable download locations
* Automatic file renaming
* Discord notifications
* Optional FFmpeg/FFprobe HEVC transcoding
* Shared configuration for the bot and transcode worker
* Example `systemd` services and timer
* Dry-run mode

---

## 🚀 Quick Start

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

For complete installation, configuration, Deluge setup, transcoding, Discord webhook, database, and `systemd` instructions, use one of the language-specific documentation files above.

---

## 📁 Main Files

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

Do not publish your real `config.json`, database, webhook URLs, or passwords.

---

## 📄 License

This project is licensed under the MIT License.
