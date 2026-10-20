# NyaaRSS Deluge Torrent Bot

A Python bot that filters a Nyaa RSS feed, adds matching torrents to Deluge, renames media files, and optionally converts video to HEVC.

## How it works

- Applies global and per-series release filters, including resolution, audio, subtitles, and optional revision tags such as v2.
- Records accepted torrents in SQLite to prevent duplicate additions. A failed download or an unconfirmed Deluge add is not recorded as a success.
- Saves torrent metadata and download locations before handing the torrent to Deluge.
- **Waits until a torrent is no longer present in Deluge before renaming or queuing its files.** Finishing the download alone does not start this stage, so active seeding files remain unchanged.
- Recovers missing file plans using exact torrent names, sizes, and SHA-1 piece checks. It does not guess releases from episode numbers or automatically redownload missing media.
- Queues optional HEVC conversion. Existing HEVC files can be skipped or remuxed for metadata changes. Audio, subtitles, and attachments are copied by default.
- Checks output video, audio/subtitle counts, and duration before replacing a source, then adds the detected codec to the filename.
- Supports optional Discord notifications and systemd journal reports, with explicit UTF-8 text handling.

## Project files

| Path | Purpose |
| --- | --- |
| bot/bot.py | RSS filtering, Deluge integration, rename and queue preparation |
| bot/torrent_recovery.py | Durable metadata and piece-verified file recovery |
| bot/transcode_worker.py | Codec inspection, remuxing, HEVC conversion and output checks |
| bot/utf8_io.py | UTF-8 console and journal output |
| bot/regex_gen.py | Generates candidate series rules from release titles |
| bot/config.example.json | Fictional series and placeholder connection settings |
| bot/test_recovery.py, bot/test_utf8.py | Regression checks for recovery and text handling |
| systemd/ | Example periodic RSS and continuous worker units |
| Extras/REGEX.md | Filtering and rename examples |

## Requirements

Python 3.10 or newer, requests, and a reachable Deluge WebUI connected to its daemon. Production execution uses Linux/Unix file locking (fcntl). FFmpeg and FFprobe are needed only for the transcode worker; its selected encoder must be available. systemd and Discord are optional integrations.

## Example configuration

The example is deliberately inactive: dry_run is enabled, transcoding and Discord are disabled, and the Deluge password is CHANGE_ME. example_user, /opt/nyaa-rss-deluge-bot, and /srv/media/downloads are placeholders, not the author's environment. The application reads a separate, private config.json relative to its working directory.

Each series key is a title-matching regular expression. The first matching rule wins. Its include_regex and exclude_regex select the release variant; set_name defines the output name, for example [ExampleGroup] Example HEVC Series - {se} becomes [ExampleGroup] Example HEVC Series - S01E03.mkv. The placeholders {season}, {episode}, and {ext} are also supported. A series must have transcode: true and global transcoding enabled to enter the codec queue.

The example uses software libx265 and retains the original as a .source-backup after successful conversion. Hardware encoding is optional and requires compatible FFmpeg support and device access.

## Runtime behavior

The RSS bot is a single-run process suitable for a timer. The worker supports one job, --all, or continuous --daemon processing. Each RSS run reloads configuration; a running worker daemon retains its startup configuration until restarted.

Metadata recovery is limited to three network requests per run by default, with unsuccessful metadata requests retried after a day. Failed conversion jobs remain marked failed; stale processing jobs can be recovered after the configured timeout. Rename collisions and files that fail torrent verification remain pending for inspection.

The repository and release ZIP contain no live configuration, watch list, download history, queue, cached torrent metadata, logs, or backups. These runtime files may contain private paths and titles and should remain local. The ZIP contains the same source snapshot as its release tag, without Git metadata.

Use the bot only for content you are authorized to download and process.
