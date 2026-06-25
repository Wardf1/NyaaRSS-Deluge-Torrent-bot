#!/usr/bin/env python3
# Wardf

import json
import re
import time
import sqlite3
import logging
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional

import requests


CONFIG_FILE = "config.json"
DB_FILE = "seen.sqlite3"
NYAA_NS = "{https://nyaa.si/xmlns/nyaa}"


logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
)


def load_config() -> Dict[str, Any]:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def init_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)
    con.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            torrent_url TEXT NOT NULL,
            added_at INTEGER NOT NULL
        )
    """)
    con.commit()
    return con


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

    # 1. Series-specific path
    if rule:
        if rule.get("download_location"):
            return rule["download_location"]

        # Backward compatibility with older configs
        if rule.get("path"):
            return rule["path"]

    # 2. Bot default path
    if cfg.get("default_download_location"):
        return cfg["default_download_location"]

    # 3. Deluge default path
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

    def add_torrent_url(self, torrent_url: str, options: Dict[str, Any]):
        return self.call("core.add_torrent_url", [torrent_url, options])


def build_deluge_options(torrent: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    options = dict(cfg.get("torrent_options", {}))

    rule = get_series_rule(torrent["title"], cfg)
    download_location = get_download_location(torrent["title"], cfg)

    # If None, do not send download_location.
    # Deluge will use its own configured default folder.
    if download_location:
        options["download_location"] = download_location

    if rule:
        if "add_paused" in rule:
            options["add_paused"] = rule["add_paused"]

        if "move_completed_path" in rule:
            options["move_completed"] = True
            options["move_completed_path"] = rule["move_completed_path"]

    return options


def main():
    cfg = load_config()
    con = init_db()

    logging.info("Fetching RSS...")
    response = requests.get(cfg["rss_url"], timeout=30)
    response.raise_for_status()

    torrents = parse_rss(response.text)
    logging.info("Found %s RSS item(s).", len(torrents))

    dry_run = cfg.get("dry_run", False)

    deluge = None

    if not dry_run:
        deluge = DelugeWeb(cfg["deluge_url"], cfg["deluge_password"])
        deluge.login()
        logging.info("Connected to Deluge WebUI.")

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
        logging.info("Path: %s", options.get("download_location", "Deluge default"))

        if dry_run:
            logging.info("DRY RUN: not adding torrent.")
        else:
            deluge.add_torrent_url(torrent["torrent_url"], options)
            mark_seen(con, torrent_id, torrent["title"], torrent["torrent_url"])
            logging.info("Added to Deluge.")

        added += 1

    logging.info(
        "Done. Added=%s, skipped_seen=%s, skipped_filters=%s",
        added,
        skipped_seen,
        skipped_filters
    )


if __name__ == "__main__":
    main()
