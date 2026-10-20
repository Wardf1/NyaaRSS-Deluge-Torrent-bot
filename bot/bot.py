#!/usr/bin/env python3
# Wardf

import base64
import json
import re
import time
import sqlite3
import logging
import subprocess
import fcntl
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional

from utf8_io import configure_utf8_output

configure_utf8_output()

import requests
import torrent_recovery as recovery


CONFIG_FILE = "config.json"
DB_FILE = "seen.sqlite3"
NYAA_NS = "{https://nyaa.si/xmlns/nyaa}"
JOURNAL_COUNTER_FILE = Path("journal_run_counter.txt")
JOURNAL_LAST_SEND_FILE = Path("journal_last_send.txt")
JOURNAL_TMP_DIR = Path("tmp")
JOURNAL_SERVICE_NAME = "nyaa-rss-deluge-bot.service"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
)


def load_config() -> Dict[str, Any]:
    with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def init_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)

    con.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            torrent_url TEXT NOT NULL,
            added_at INTEGER NOT NULL,
            renamed INTEGER NOT NULL DEFAULT 0
        )
    """)

    columns = {
        row[1]
        for row in con.execute("PRAGMA table_info(seen)").fetchall()
    }

    if "renamed" not in columns:
        con.execute(
            "ALTER TABLE seen ADD COLUMN renamed INTEGER NOT NULL DEFAULT 0"
        )

    if "rename_plan" not in columns:
        con.execute(
            "ALTER TABLE seen ADD COLUMN rename_plan TEXT"
        )

    con.commit()
    recovery.init_storage(con)
    return con


def get_pending_renames(con: sqlite3.Connection):
    return con.execute("""
        SELECT id, title, rename_plan
        FROM seen
        WHERE renamed = 0
        ORDER BY added_at DESC
    """).fetchall()


def save_rename_plan(
    con: sqlite3.Connection,
    torrent_id: str,
    rename_plan
) -> None:
    con.execute(
        "UPDATE seen SET rename_plan = ? WHERE id = ?",
        (json.dumps(rename_plan, ensure_ascii=False), torrent_id)
    )
    con.commit()


def mark_renamed(con: sqlite3.Connection, torrent_id: str) -> None:
    con.execute(
        "UPDATE seen SET renamed = 1 WHERE id = ?",
        (torrent_id,)
    )
    con.commit()

def is_seen(con: sqlite3.Connection, torrent_id: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM seen WHERE id = ?",
        (torrent_id,)
    ).fetchone()
    return row is not None


def mark_seen(con: sqlite3.Connection, torrent_id: str, title: str, url: str) -> None:
    con.execute(
        "INSERT OR IGNORE INTO seen(id, title, torrent_url, added_at) VALUES (?, ?, ?, ?)",
        (torrent_id, title, url, int(time.time()))
    )
    con.commit()


def item_text(item: ET.Element, tag: str, default: str = "") -> str:
    el = item.find(tag)
    return el.text.strip() if el is not None and el.text else default


def nyaa_text(item: ET.Element, tag: str, default: str = "") -> str:
    return item_text(item, NYAA_NS + tag, default)


def parse_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def fetch_rss(
    url: str,
    attempts: int = 3,
    connect_timeout: int = 20,
    read_timeout: int = 60,
    retry_delay: int = 10
) -> str:
    last_error: Optional[Exception] = None

    headers = {
        "User-Agent": "Nyaa-RSS-Deluge-Bot/1.0"
    }

    for attempt in range(1, attempts + 1):
        try:
            logging.info(
                "Fetching RSS, attempt %s/%s...",
                attempt,
                attempts
            )

            response = requests.get(
                url,
                headers=headers,
                timeout=(connect_timeout, read_timeout)
            )
            response.raise_for_status()

            return response.content.decode("utf-8-sig")

        except (
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ReadTimeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError
        ) as error:
            last_error = error

            logging.warning(
                "RSS fetch failed on attempt %s/%s: %s",
                attempt,
                attempts,
                error
            )

            if attempt < attempts:
                time.sleep(retry_delay * attempt)

    raise RuntimeError(
        f"Could not fetch RSS after {attempts} attempts: {last_error}"
    )



def parse_rss(xml_text: str):
    root = ET.fromstring(xml_text)
    channel = root.find("channel")

    if channel is None:
        return []

    torrents = []

    for item in channel.findall("item"):
        title = item_text(item, "title")
        torrent_url = item_text(item, "link")
        guid = item_text(item, "guid")
        info_hash = nyaa_text(item, "infoHash")

        torrents.append({
            "title": title,
            "torrent_url": torrent_url,
            "guid": guid,
            "info_hash": info_hash,
            "id": info_hash or guid or torrent_url,
            "category": nyaa_text(item, "category"),
            "category_id": nyaa_text(item, "categoryId"),
            "size": nyaa_text(item, "size"),
            "seeders": parse_int(nyaa_text(item, "seeders", "0")),
            "leechers": parse_int(nyaa_text(item, "leechers", "0")),
            "downloads": parse_int(nyaa_text(item, "downloads", "0")),
            "trusted": nyaa_text(item, "trusted", "No").lower() == "yes",
            "remake": nyaa_text(item, "remake", "No").lower() == "yes",
        })

    return torrents


def regex_any(patterns, text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def get_series_rule(title: str, cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for pattern, rule in cfg.get("series", {}).items():
        if re.search(pattern, title, re.IGNORECASE):
            rule = dict(rule)
            rule["_matched_pattern"] = pattern
            return rule

    return None


def get_download_location(title: str, cfg: Dict[str, Any]) -> Optional[str]:
    rule = get_series_rule(title, cfg)

    if rule:
        if rule.get("download_location"):
            return rule["download_location"]

        if rule.get("path"):
            return rule["path"]

    if cfg.get("default_download_location"):
        return cfg["default_download_location"]

    return None


def matches_global_filters(torrent: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    title = torrent["title"]

    if cfg.get("trusted_only", False) and not torrent["trusted"]:
        return False

    if cfg.get("skip_remakes", True) and torrent["remake"]:
        return False

    if torrent["seeders"] < cfg.get("min_seeders", 0):
        return False

    include = cfg.get("include_regex", [])
    exclude = cfg.get("exclude_regex", [])

    if include and not regex_any(include, title):
        return False

    if exclude and regex_any(exclude, title):
        return False

    return True


def matches_series_filters(torrent: Dict[str, Any], rule: Optional[Dict[str, Any]]) -> bool:
    if not rule:
        return True

    title = torrent["title"]

    if rule.get("trusted_only", False) and not torrent["trusted"]:
        return False

    if rule.get("skip_remakes", False) and torrent["remake"]:
        return False

    if torrent["seeders"] < rule.get("min_seeders", 0):
        return False

    include = rule.get("include_regex", [])
    exclude = rule.get("exclude_regex", [])

    if include and not regex_any(include, title):
        return False

    if exclude and regex_any(exclude, title):
        return False

    return True


def should_add(torrent: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    if not matches_global_filters(torrent, cfg):
        return False

    rule = get_series_rule(torrent["title"], cfg)

    if cfg.get("only_configured_series", False) and not rule:
        return False

    if not matches_series_filters(torrent, rule):
        return False

    return True


class DelugeWeb:
    def __init__(self, base_url: str, password: str):
        self.url = base_url.rstrip("/") + "/json"
        self.password = password
        self.session = requests.Session()
        self.request_id = 0

    def call(self, method: str, params=None):
        self.request_id += 1

        payload = {
            "method": method,
            "params": params or [],
            "id": self.request_id,
        }

        r = self.session.post(self.url, json=payload, timeout=30)
        r.raise_for_status()

        data = r.json()

        if data.get("error"):
            raise RuntimeError(data["error"])

        return data.get("result")

    def login(self):
        result = self.call("auth.login", [self.password])

        if not result:
            raise RuntimeError("Deluge WebUI login failed")

    def ensure_daemon_connected(self) -> None:
        if self.call("web.connected", []):
            return

        hosts = self.call("web.get_hosts", []) or []
        if not hosts:
            raise RuntimeError("Deluge WebUI has no configured daemon hosts")

        last_error = None
        for host in hosts:
            if not isinstance(host, (list, tuple)) or not host:
                continue
            host_id = host[0]
            try:
                self.call("web.connect", [host_id])
                if self.call("web.connected", []):
                    logging.info("Deluge WebUI connected to daemon: %s", host_id)
                    return
            except Exception as error:
                last_error = error

        raise RuntimeError(
            f"Deluge WebUI could not connect to any daemon host: {last_error}"
        )

    def add_torrent_url(self, torrent_url: str, options: Dict[str, Any], prepare=None):
        r = self.session.get(torrent_url, timeout=30)
        r.raise_for_status()

        metadata = recovery.decode_torrent(r.content)
        if prepare is not None:
            # Commit metadata before Deluge can finish and auto-remove a torrent.
            prepare(metadata)
        torrent_name = torrent_url.rstrip("/").split("/")[-1]
        torrent_data = base64.b64encode(r.content).decode("utf-8")
        result = self.call("core.add_torrent_file", [
            torrent_name, torrent_data, options
        ])
        if not result:
            existing = self.call("core.get_torrent_status", [
                metadata["hash"], ["name"]
            ])
            if not existing or not existing.get("name"):
                raise RuntimeError("Deluge did not confirm adding the torrent")
            return metadata["hash"]
        if str(result).lower() != metadata["hash"]:
            raise RuntimeError("Deluge returned an unexpected torrent hash")
        return result

    def get_torrent_status(self, torrent_id: str) -> Dict[str, Any]:
        self.ensure_daemon_connected()
        return self.call("web.get_torrent_status", [
            torrent_id,
            [
                "name",
                "progress",
                "state",
                "files",
                "save_path"
            ]
        ])


    def rename_files(self, torrent_id: str, files) -> Any:
        return self.call("core.rename_files", [
            torrent_id,
            files
        ])

def build_deluge_options(torrent: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    options = dict(cfg.get("torrent_options", {}))

    rule = get_series_rule(torrent["title"], cfg)
    download_location = get_download_location(torrent["title"], cfg)

    if download_location:
        options["download_location"] = download_location

    if rule:
        if "add_paused" in rule:
            options["add_paused"] = rule["add_paused"]

        if "move_completed_path" in rule:
            options["move_completed"] = True
            options["move_completed_path"] = rule["move_completed_path"]

    return options



def send_discord_webhook(torrent: Dict[str, Any], options: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    webhook_cfg = cfg.get("discord_webhook", {})

    if not webhook_cfg.get("enabled", False):
        return

    webhook_url = webhook_cfg.get("url")
    if not webhook_url:
        return

    payload = {
        "username": webhook_cfg.get("username", "Nyaa RSS Bot"),
        "embeds": [
            {
                "title": "đź“Ą Torrent added to Deluge",
                "description": torrent.get("title", "Unknown")[:4096],
                "url": torrent.get("guid") or torrent.get("torrent_url"),
                "color": 5814783,
                "fields": [
                    {
                        "name": "đź“„ File name",
                        "value": torrent.get("title", "Unknown")[:1024],
                        "inline": False
                    },
                    {
                        "name": "đź’ľ Size",
                        "value": torrent.get("size", "Unknown"),
                        "inline": True
                    },
                    {
                        "name": "đźŚ± Seeders",
                        "value": str(torrent.get("seeders", 0)),
                        "inline": True
                    },
                    {
                        "name": "đź‘Ą Leechers",
                        "value": str(torrent.get("leechers", 0)),
                        "inline": True
                    },
                    {
                        "name": "đź“‚ Download path",
                        "value": options.get("download_location", "Deluge default")[:1024],
                        "inline": False
                    },
                    {
                        "name": "đźŹ·ď¸Ź Category",
                        "value": torrent.get("category", "Unknown"),
                        "inline": True
                    },
                    {
                        "name": "âś… Trusted",
                        "value": "Yes" if torrent.get("trusted") else "No",
                        "inline": True
                    },
                    {
                        "name": "â™»ď¸Ź Remake",
                        "value": "Yes" if torrent.get("remake") else "No",
                        "inline": True
                    },
                    {
                        "name": "đź”— Torrent",
                        "value": f"[Download .torrent]({torrent.get('torrent_url')})",
                        "inline": False
                    },
                    {
                        "name": "đź§¬ Info Hash",
                        "value": f"`{torrent.get('info_hash', 'Unknown')}`",
                        "inline": False
                    }
                ],
                "footer": {
                    "text": "Nyaa RSS Deluge Bot"
                },
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }
        ]
    }

    try:
        r = requests.post(webhook_url, json=payload, timeout=15)
        r.raise_for_status()
        logging.info("Discord webhook sent.")
    except Exception as e:
        logging.warning("Discord webhook failed: %s", e)


def send_discord_log_webhook(cfg: Dict[str, Any], title: str, message: str, color: int = 15158332) -> None:
    webhook_cfg = cfg.get("discord_journal_webhook", {})

    if not webhook_cfg.get("enabled", False):
        return

    webhook_url = webhook_cfg.get("url")
    if not webhook_url:
        return

    payload = {
        "username": webhook_cfg.get("username", "Nyaa RSS Bot Logs"),
        "embeds": [
            {
                "title": title,
                "description": message[:4096],
                "color": color,
                "footer": {
                    "text": "Nyaa RSS Deluge Bot"
                },
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }
        ]
    }

    try:
        r = requests.post(webhook_url, json=payload, timeout=15)
        r.raise_for_status()
        logging.info("Discord log webhook sent.")
    except Exception as e:
        logging.warning("Discord log webhook failed: %s", e)      

def read_integer_file(path: Path, default: int = 0) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        return default


def write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(value, encoding="utf-8")
    temporary_path.replace(path)


def get_journal_period() -> tuple[int, int]:
    now = int(time.time())

    try:
        last_send = int(
            JOURNAL_LAST_SEND_FILE.read_text(encoding="utf-8").strip()
        )
    except (FileNotFoundError, ValueError, OSError):
        # Przy pierwszym raporcie pobierz logi z ostatniej godziny.
        last_send = now - 3600

    return last_send, now


def collect_systemd_journal(
    service_name: str,
    since_timestamp: int,
    until_timestamp: int
) -> str:
    since_text = datetime.fromtimestamp(since_timestamp).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    until_text = datetime.fromtimestamp(until_timestamp).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    command = [
        "journalctl",
        "-u",
        service_name,
        "--since",
        since_text,
        "--until",
        until_text,
        "--no-pager",
        "-o",
        "short-iso"
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False
    )

    if result.returncode != 0:
        error = result.stderr.strip() or "Unknown journalctl error"
        raise RuntimeError(f"journalctl failed: {error}")

    return result.stdout


def limit_log_size(log_text: str, max_size_bytes: int) -> str:
    encoded = log_text.encode("utf-8")

    if len(encoded) <= max_size_bytes:
        return log_text

    # Zachowaj koĹ„cowÄ…, najnowszÄ… czÄ™Ĺ›Ä‡ logu.
    limited = encoded[-max_size_bytes:].decode(
        "utf-8",
        errors="replace"
    )

    return (
        "Log was truncated because it exceeded the configured size limit.\n\n"
        + limited
    )


def send_discord_journal_file(
    cfg: Dict[str, Any],
    file_path: Path,
    since_timestamp: int,
    until_timestamp: int,
    run_count: int
) -> None:
    webhook_cfg = cfg.get("discord_journal_webhook", {})

    if not webhook_cfg.get("enabled", False):
        return

    webhook_url = webhook_cfg.get("url")

    if not webhook_url:
        raise RuntimeError("Discord journal webhook URL is missing")

    since_text = datetime.fromtimestamp(since_timestamp).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    until_text = datetime.fromtimestamp(until_timestamp).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    payload = {
        "username": webhook_cfg.get(
            "username",
            "Nyaa RSS Bot Logs"
        ),
        "content": (
            f"đź“„ **Nyaa RSS bot journal**\n"
            f"Period: `{since_text}` â€“ `{until_text}`\n"
            f"Runs: `{run_count}`"
        )
    }

    with file_path.open("rb") as log_file:
        response = requests.post(
            webhook_url,
            data={
                "payload_json": json.dumps(payload)
            },
            files={
                "files[0]": (
                    file_path.name,
                    log_file,
                    "text/plain"
                )
            },
            timeout=60
        )

    response.raise_for_status()


def process_journal_counter(cfg: Dict[str, Any]) -> None:
    webhook_cfg = cfg.get("discord_journal_webhook", {})

    if not webhook_cfg.get("enabled", False):
        return

    send_after_runs = int(webhook_cfg.get("send_after_runs", 30))

    if send_after_runs < 1:
        logging.warning(
            "discord_journal_webhook.send_after_runs must be at least 1"
        )
        return

    current_count = read_integer_file(
        JOURNAL_COUNTER_FILE,
        default=0
    ) + 1

    write_text_atomic(
        JOURNAL_COUNTER_FILE,
        str(current_count)
    )

    logging.info(
        "Journal counter: %s/%s",
        current_count,
        send_after_runs
    )

    if current_count < send_after_runs:
        return

    since_timestamp, until_timestamp = get_journal_period()

    log_text = collect_systemd_journal(
        JOURNAL_SERVICE_NAME,
        since_timestamp,
        until_timestamp
    )

    if not log_text.strip():
        if not webhook_cfg.get("send_empty_logs", False):
            logging.info(
                "Journal is empty; report will not be sent."
            )

            # Okres uznajemy za obsĹ‚uĹĽony.
            write_text_atomic(
                JOURNAL_LAST_SEND_FILE,
                str(until_timestamp)
            )
            write_text_atomic(
                JOURNAL_COUNTER_FILE,
                "0"
            )
            return

        log_text = "No journal entries were recorded during this period.\n"

    max_size_mb = float(
        webhook_cfg.get("max_file_size_mb", 7)
    )
    max_size_bytes = max(
        1024,
        int(max_size_mb * 1024 * 1024)
    )

    log_text = limit_log_size(
        log_text,
        max_size_bytes
    )

    JOURNAL_TMP_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    file_name = (
        "nyaa-rss-deluge-bot_"
        f"{datetime.fromtimestamp(since_timestamp).strftime('%Y-%m-%d_%H-%M-%S')}"
        "_to_"
        f"{datetime.fromtimestamp(until_timestamp).strftime('%Y-%m-%d_%H-%M-%S')}"
        ".log"
    )

    temporary_log = JOURNAL_TMP_DIR / file_name

    header = (
        "Nyaa RSS Deluge Bot journal\n"
        f"Service: {JOURNAL_SERVICE_NAME}\n"
        f"Runs: {current_count}\n"
        f"From: {datetime.fromtimestamp(since_timestamp)}\n"
        f"To: {datetime.fromtimestamp(until_timestamp)}\n"
        + "=" * 80
        + "\n\n"
    )

    temporary_log.write_text(
        header + log_text,
        encoding="utf-8"
    )

    try:
        send_discord_journal_file(
            cfg,
            temporary_log,
            since_timestamp,
            until_timestamp,
            current_count
        )

        # Reset dopiero po poprawnym wysĹ‚aniu.
        write_text_atomic(
            JOURNAL_LAST_SEND_FILE,
            str(until_timestamp)
        )
        write_text_atomic(
            JOURNAL_COUNTER_FILE,
            "0"
        )

        logging.info(
            "Journal file sent to Discord. Counter reset."
        )

    finally:
        try:
            temporary_log.unlink(missing_ok=True)
        except OSError as error:
            logging.warning(
                "Could not remove temporary journal file: %s",
                error
            )

def extract_episode_data(title: str, rule: Dict[str, Any]) -> Optional[Dict[str, str]]:
    season_episode = re.search(
        r"\bS(\d{1,2})E(\d{1,3})\b",
        title,
        re.IGNORECASE
    )

    if season_episode:
        season = int(season_episode.group(1))
        episode = int(season_episode.group(2))
    else:
        episode_only = re.search(
            r"\s-\s(\d{1,3})(?:\s|\[|\(|$)",
            title
        )

        if not episode_only:
            return None

        season = int(rule.get("default_season", 1))
        episode = int(episode_only.group(1))

    return {
        "season": f"{season:02d}",
        "episode": f"{episode:02d}",
        "se": f"S{season:02d}E{episode:02d}"
    }


def build_new_file_name(
    original_rss_title: str,
    current_file_name: str,
    rule: Dict[str, Any]
) -> Optional[str]:
    template = rule.get("set_name")

    if not template:
        return None

    episode_data = extract_episode_data(original_rss_title, rule)

    if not episode_data:
        logging.warning(
            "Cannot extract episode number from: %s",
            original_rss_title
        )
        return None

    extension = ""

    if "." in current_file_name:
        extension = current_file_name.rsplit(".", 1)[-1]

    values = {
        **episode_data,
        "ext": extension
    }

    try:
        new_name = template.format(**values)
    except KeyError as e:
        logging.warning("Unknown set_name placeholder: %s", e)
        return None

    if extension and not new_name.lower().endswith(f".{extension.lower()}"):
        new_name += f".{extension}"

    return new_name


def build_filesystem_rename_plan(
    original_rss_title: str,
    status: Dict[str, Any],
    rule: Dict[str, Any]
):
    # Wardf
    save_path = status.get("save_path")
    files = status.get("files", [])

    if not save_path or not files:
        return []

    base_path = Path(save_path)
    plan = []

    for file_data in files:
        current_path = str(file_data.get("path", "")).strip()

        if not current_path:
            continue

        current_name = Path(current_path).name

        if not current_name.lower().endswith(
            (".mkv", ".mp4", ".avi", ".m4v")
        ):
            continue

        new_name = build_new_file_name(
            original_rss_title,
            current_name,
            rule
        ) or current_name

        relative_source = Path(current_path)
        relative_target = relative_source.with_name(new_name)

        source = base_path / relative_source
        target = base_path / relative_target

        plan.append({
            "source": str(source),
            "target": str(target)
        })

    return plan



def get_transcode_queue_path(cfg: Dict[str, Any]) -> Path:
    transcode_cfg = cfg.get("transcode", {})
    return Path(transcode_cfg.get("queue_file", "transcode_queue.json"))


def enqueue_transcode_job(
    cfg: Dict[str, Any],
    torrent_id: str,
    original_rss_title: str,
    file_path: Path
) -> bool:
    # Wardf
    transcode_cfg = cfg.get("transcode", {})

    if not transcode_cfg.get("enabled", False):
        return True

    rule = get_series_rule(original_rss_title, cfg) or {}

    if not rule.get("transcode", True):
        logging.info(
            "Transcode disabled for series; file will not be queued: %s",
            file_path
        )
        return True

    if file_path.suffix.lower() not in {".mkv", ".mp4", ".avi", ".m4v"}:
        return True

    queue_path = get_transcode_queue_path(cfg)
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = queue_path.with_suffix(queue_path.suffix + ".lock")

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

        try:
            try:
                jobs = json.loads(queue_path.read_text(encoding="utf-8"))
                if not isinstance(jobs, list):
                    raise ValueError("Invalid transcode queue: expected a list")
            except FileNotFoundError:
                jobs = []

            normalized_path = str(file_path.resolve(strict=False))

            for job in jobs:
                if (
                    str(job.get("file_path", "")) == normalized_path
                    and job.get("torrent_id") == torrent_id
                    and job.get("status") in {"queued", "processing", "done"}
                ):
                    logging.info("Transcode job already exists: %s", file_path)
                    return True

            if any(
                str(job.get("file_path", "")) == normalized_path
                and job.get("status") in {"queued", "processing"}
                for job in jobs
            ):
                raise RuntimeError(f"Another torrent is processing this path: {file_path}")

            jobs.append({
                "job_id": f"{torrent_id}:{int(time.time())}:{file_path.name}",
                "torrent_id": torrent_id,
                "original_title": original_rss_title,
                "series_pattern": rule.get("_matched_pattern", ""),
                "file_path": normalized_path,
                "status": "queued",
                "attempts": 0,
                "queued_at": int(time.time()),
                "last_error": ""
            })

            temporary_path = queue_path.with_suffix(queue_path.suffix + ".tmp")
            temporary_path.write_text(
                json.dumps(jobs, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8"
            )
            temporary_path.replace(queue_path)

            logging.info("Queued file for codec check/transcode: %s", file_path)
            return True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def execute_filesystem_rename_plan(
    con: sqlite3.Connection,
    torrent_id: str,
    original_rss_title: str,
    rename_plan_text: Optional[str],
    cfg: Dict[str, Any]
) -> None:
    # Wardf
    if not rename_plan_text:
        logging.warning(
            "Torrent is no longer in Deluge, but no file path was saved: %s",
            original_rss_title
        )
        return

    try:
        rename_plan = json.loads(rename_plan_text)
    except (TypeError, json.JSONDecodeError):
        logging.warning(
            "Invalid saved rename plan for torrent: %s",
            torrent_id
        )
        return

    if not rename_plan:
        mark_renamed(con, torrent_id)
        return

    all_done = True
    final_paths = []

    for operation in rename_plan:
        source = Path(operation.get("source", ""))
        target = Path(operation.get("target", ""))

        if not source.name or not target.name:
            all_done = False
            continue

        if source == target:
            if source.exists():
                final_paths.append(source)
            else:
                logging.warning("Expected media file does not exist: %s", source)
                all_done = False
            continue

        if target.exists() and not source.exists():
            logging.info("Target file already exists: %s", target)
            final_paths.append(target)
            continue

        if target.exists() and source.exists():
            logging.warning(
                "Cannot rename because both source and target exist: %s | %s",
                source,
                target
            )
            all_done = False
            continue

        if not source.exists():
            logging.warning("Source file does not exist: %s", source)
            all_done = False
            continue

        if source.suffix.lower() in {".part", ".tmp"}:
            logging.warning("Temporary file will not be renamed: %s", source)
            all_done = False
            continue

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            final_paths.append(target)
            logging.info(
                "Filesystem rename completed: %s -> %s",
                source.name,
                target.name
            )
        except OSError as error:
            logging.warning(
                "Filesystem rename failed: %s -> %s: %s",
                source,
                target,
                error
            )
            all_done = False

    if all_done:
        queue_ok = True
        for final_path in final_paths:
            if not enqueue_transcode_job(
                cfg, torrent_id, original_rss_title, final_path
            ):
                queue_ok = False

        if queue_ok:
            mark_renamed(con, torrent_id)
            logging.info(
                "Filesystem stage completed and transcode jobs queued: %s",
                original_rss_title
            )


def process_completed_renames(
    con: sqlite3.Connection,
    deluge: DelugeWeb,
    cfg: Dict[str, Any]
) -> None:
    deluge.ensure_daemon_connected()
    statuses = deluge.call("core.get_torrents_status", [
        {}, ["name", "files", "save_path", "progress", "state"]
    ])
    if not isinstance(statuses, dict):
        raise RuntimeError("Cannot obtain an authoritative Deluge torrent list")

    budget = [max(0, int(cfg.get("recovery_metadata_per_run", 3)))]
    waiting = 0
    recovered = 0
    for torrent_id, title, plan_text in get_pending_renames(con):
        try:
            rule = get_series_rule(title, cfg)
            if rule is None:
                # A temporary configuration change must not discard pending work.
                continue
            status = statuses.get(torrent_id)
            if status is not None:
                plan = build_filesystem_rename_plan(title, status, rule)
                if plan:
                    save_rename_plan(con, torrent_id, plan)
                metadata, locations = recovery.source_record(con, torrent_id)
                if metadata and status.get("save_path"):
                    recovery.store_source(
                        con, torrent_id, metadata,
                        [status["save_path"]] + locations
                    )
                # Preserve seeding: never modify files still managed by Deluge.
                continue

            url = con.execute(
                "SELECT torrent_url FROM seen WHERE id=?", (torrent_id,)
            ).fetchone()[0]
            options = build_deluge_options({"title": title}, cfg)
            metadata, locations = recovery.recover_source(
                con, torrent_id, url, cfg, options, budget
            )
            if metadata is None:
                waiting += 1
                continue

            # Finish an interrupted handoff without submitting the same jobs again.
            queue_path = get_transcode_queue_path(cfg)
            if queue_path.exists():
                lock_path = queue_path.with_suffix(queue_path.suffix + ".lock")
                with lock_path.open("a+", encoding="utf-8") as lock_file:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
                    try:
                        jobs = json.loads(queue_path.read_text(encoding="utf-8"))
                        if not isinstance(jobs, list):
                            raise ValueError("Invalid transcode queue")
                    finally:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                handled = {
                    job.get("file_path") for job in jobs
                    if job.get("torrent_id") == torrent_id
                    and job.get("status") in {"queued", "processing", "done"}
                }
                if plan_text:
                    expected = {op["target"] for op in json.loads(plan_text)}
                    if expected and expected <= handled:
                        mark_renamed(con, torrent_id)
                        continue

            plan = recovery.verified_plan(
                metadata, locations, title, rule, plan_text, build_new_file_name
            )
            if plan is None:
                waiting += 1
                con.execute(
                    "UPDATE torrent_sources SET last_error=? WHERE id=?",
                    ("No complete file matching torrent piece hashes at saved paths", torrent_id)
                )
                con.commit()
                continue
            # Recheck presence after hashing, which may take time on large files.
            current = deluge.call("core.get_torrents_status", [{}, ["name"]])
            if not isinstance(current, dict):
                raise RuntimeError("Cannot confirm torrent is still absent")
            if torrent_id in current:
                continue
            save_rename_plan(con, torrent_id, plan)
            execute_filesystem_rename_plan(
                con, torrent_id, title, json.dumps(plan), cfg
            )
            recovered += 1
        except Exception:
            # A corrupt file or temporary RPC/network error must not starve
            # every torrent later in the database.
            logging.exception("Pending torrent processing failed: %s", torrent_id)
    logging.info(
        "Recovery pass: handled=%s, awaiting metadata or complete files=%s",
        recovered, waiting
    )


def main():
    cfg = load_config()
    con = init_db()

    try:
        dry_run = cfg.get("dry_run", False)
        deluge = None

        # Najpierw Deluge i zalegĹ‚e zmiany nazw.
        if not dry_run:
            deluge = DelugeWeb(
                cfg["deluge_url"],
                cfg["deluge_password"]
            )
            deluge.login()
            deluge.ensure_daemon_connected()
            logging.info("Connected to Deluge WebUI and daemon.")

            try:
                process_completed_renames(con, deluge, cfg)
            except Exception:
                logging.exception(
                    "Filesystem/transcode queue preparation failed; "
                    "continuing with RSS processing."
                )
        else:
            logging.info("DRY RUN mode enabled.")

        # Dopiero pĂłĹşniej pobieranie RSS.
        rss_text = fetch_rss(
            cfg["rss_url"],
            attempts=int(cfg.get("rss_retry_attempts", 3)),
            connect_timeout=int(cfg.get("rss_connect_timeout", 20)),
            read_timeout=int(cfg.get("rss_read_timeout", 60)),
            retry_delay=int(cfg.get("rss_retry_delay", 10))
        )

        torrents = parse_rss(rss_text)
        logging.info("Found %s RSS item(s).", len(torrents))

        added = 0
        skipped_seen = 0
        skipped_filters = 0

        for torrent in torrents:
            torrent_id = torrent["id"]

            if is_seen(con, torrent_id):
                skipped_seen += 1
                continue

            if not should_add(torrent, cfg):
                skipped_filters += 1
                continue

            options = build_deluge_options(torrent, cfg)

            logging.info("Matched: %s", torrent["title"])
            logging.info(
                "Path: %s",
                options.get("download_location", "Deluge default")
            )

            if dry_run:
                logging.info("DRY RUN: not adding torrent.")
                added += 1
                continue

            try:
                if deluge is None:
                    raise RuntimeError("Deluge client is not initialized")

                if not options.get("download_location"):
                    options["download_location"] = deluge.call(
                        "core.get_config_value", ["download_location"]
                    )
                # Persist Deluge's global move-on-completion destination too.
                if "move_completed" not in options:
                    options["move_completed"] = deluge.call(
                        "core.get_config_value", ["move_completed"]
                    )
                if options.get("move_completed") and not options.get("move_completed_path"):
                    options["move_completed_path"] = deluge.call(
                        "core.get_config_value", ["move_completed_path"]
                    )

                def prepare(metadata):
                    recovery.store_source(
                        con, torrent_id, metadata,
                        recovery.download_locations(cfg, options)
                    )

                added_torrent_id = deluge.add_torrent_url(
                    torrent["torrent_url"], options, prepare=prepare
                )

                logging.info(
                    "Added to Deluge: %s",
                    added_torrent_id
                )

            except requests.exceptions.HTTPError as error:
                status_code = (
                    error.response.status_code
                    if error.response is not None
                    else None
                )

                if status_code == 404:
                    logging.warning(
                        "Torrent file returned HTTP 404 and will be skipped: %s",
                        torrent["torrent_url"]
                    )

                    # A failed download is not an accepted torrent.
                    # Retry while the item remains in RSS.
                    continue

                logging.warning(
                    "Could not download torrent file; item will be retried later: %s: %s",
                    torrent["title"],
                    error
                )
                continue

            except requests.exceptions.RequestException as error:
                logging.warning(
                    "Torrent download request failed; item will be retried later: %s: %s",
                    torrent["title"],
                    error
                )
                continue

            except (RuntimeError, ValueError) as error:
                message = str(error)

                if "Torrent already in session" in message:
                    logging.warning(
                        "Torrent already in Deluge, marking as seen."
                    )

                    added_torrent_id = torrent_id
                else:
                    logging.warning(
                        "Deluge rejected torrent; item will be retried later: %s: %s",
                        torrent["title"],
                        error
                    )
                    continue

            mark_seen(
                con,
                torrent_id,
                torrent["title"],
                torrent["torrent_url"]
            )

            metadata, locations = recovery.source_record(con, torrent_id)
            if metadata and locations:
                plan = build_filesystem_rename_plan(
                    torrent["title"],
                    {"save_path": locations[0], "files": metadata["files"]},
                    get_series_rule(torrent["title"], cfg) or {}
                )
                save_rename_plan(con, torrent_id, plan)

            send_discord_webhook(
                torrent,
                options,
                cfg
            )

            added += 1

        logging.info(
            "Done. Added=%s, skipped_seen=%s, skipped_filters=%s",
            added,
            skipped_seen,
            skipped_filters
        )

    finally:
        try:
            process_journal_counter(cfg)
        except Exception as error:
            logging.warning(
                "Journal counter processing failed: %s",
                error
            )

        con.close()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            cfg = load_config()
            send_discord_log_webhook(
                cfg,
                "âťŚ Nyaa RSS Bot error",
                f"```text\n{type(e).__name__}: {e}\n```"
            )
        except Exception:
            pass

        logging.exception("Unhandled exception")
        raise