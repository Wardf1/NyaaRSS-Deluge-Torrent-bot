# RSS Deluge Bot

Simple Python bot for automatically adding torrents from an RSS feed to Deluge WebUI.

The bot monitors an RSS feed, filters releases using configurable rules, prevents duplicate downloads using SQLite, and automatically adds matching torrents to Deluge through its WebUI API.

> Use only with content you are legally allowed to download.

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
* Deluge WebUI integration
* Duplicate protection using SQLite
* Per-series filtering
* Configurable download locations
* Regex-based title matching
* Release group filtering
* Dry-run mode
* Nyaa RSS support

---

## 🚀 Quick Start

Clone the repository:

```bash
git clone https://github.com/Wardf1/NyaaRSS-Deluge-Torrent-bot.git
cd NyaaRSS-Deluge-Torrent-bot
```

Install dependencies:

```bash
pip install requests
```

Copy the example configuration:

```bash
cp bot/config.example.json bot/config.json
```

Edit your configuration:

```bash
nano bot/config.json
```

Run the bot:

```bash
python3 bot.py
```

For complete installation, configuration, and usage instructions, see one of the language-specific documentation files above.

---

## 📄 License

This project is licensed under the MIT License.
