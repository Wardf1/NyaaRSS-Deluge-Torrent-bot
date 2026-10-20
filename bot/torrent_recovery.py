"""Durable torrent metadata and exact piece-verified file recovery."""
import hashlib
import json
import logging
import time
from pathlib import Path, PurePosixPath
import requests

MEDIA = {".mkv", ".mp4", ".avi", ".m4v"}


def decode_torrent(raw):
    position = 0
    info_bytes = None

    def parse(depth=0):
        nonlocal position, info_bytes
        if depth > 64 or position >= len(raw):
            raise ValueError("Invalid torrent structure")
        token = raw[position:position + 1]
        if token == b"i":
            end = raw.index(b"e", position)
            value = int(raw[position + 1:end])
            position = end + 1
            return value
        if token in (b"l", b"d"):
            position += 1
            value = [] if token == b"l" else {}
            while raw[position:position + 1] != b"e":
                if token == b"l":
                    value.append(parse(depth + 1))
                else:
                    key = parse(depth + 1)
                    if not isinstance(key, bytes) or key in value:
                        raise ValueError("Invalid torrent dictionary")
                    start = position
                    value[key] = parse(depth + 1)
                    if depth == 0 and key == b"info":
                        info_bytes = raw[start:position]
            position += 1
            return value
        colon = raw.index(b":", position)
        size = int(raw[position:colon])
        position = colon + 1
        if size < 0 or position + size > len(raw):
            raise ValueError("Invalid torrent string")
        value = raw[position:position + size]
        position += size
        return value

    torrent = parse()
    if position != len(raw) or not isinstance(torrent, dict) or info_bytes is None:
        raise ValueError("Missing torrent info")
    info = torrent[b"info"]

    def component(value):
        name = value.decode("utf-8")
        if not name or name in {".", ".."} or any(c in name for c in "/\\\0:"):
            raise ValueError("Unsafe torrent path")
        return name

    name = component(info.get(b"name.utf-8", info.get(b"name")))
    files = []
    for item in info.get(b"files", [{b"length": info.get(b"length"), b"path": []}]):
        parts = [component(p) for p in item.get(b"path.utf-8", item[b"path"])]
        size = item[b"length"]
        if not isinstance(size, int) or size < 0:
            raise ValueError("Invalid torrent file size")
        files.append({"path": str(PurePosixPath(name, *parts)), "size": size})
    piece_length = info.get(b"piece length", 0)
    pieces = info.get(b"pieces", b"")
    total = sum(f["size"] for f in files)
    if (not isinstance(piece_length, int) or piece_length <= 0 or
            len(pieces) != 20 * ((total + piece_length - 1) // piece_length)):
        raise ValueError("Unsupported or invalid v1 torrent hashes")
    return {"hash": hashlib.sha1(info_bytes).hexdigest(), "files": files,
            "piece_length": piece_length, "pieces": pieces.hex()}


def init_storage(con):
    con.execute("""CREATE TABLE IF NOT EXISTS torrent_sources (
        id TEXT PRIMARY KEY, metadata TEXT, locations TEXT,
        checked_at INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT ''
    )""")
    con.commit()


def store_source(con, torrent_id, metadata, locations):
    if metadata["hash"].lower() != torrent_id.lower():
        raise ValueError("Torrent info hash does not match RSS/history")
    con.execute("""INSERT INTO torrent_sources(id, metadata, locations, checked_at)
        VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET
        metadata=excluded.metadata, locations=excluded.locations,
        checked_at=excluded.checked_at, last_error=''""",
        (torrent_id, json.dumps(metadata), json.dumps(list(dict.fromkeys(locations))), int(time.time())))
    con.commit()


def source_record(con, torrent_id):
    row = con.execute("SELECT metadata, locations FROM torrent_sources WHERE id=?", (torrent_id,)).fetchone()
    if row and row[0]:
        return json.loads(row[0]), json.loads(row[1] or "[]")
    return None, []


def download_locations(cfg, options=None):
    options = options or {}
    return list(dict.fromkeys(str(p) for p in (
        options.get("download_location"), options.get("move_completed_path"),
        cfg.get("default_download_location"),
    ) if p))


def recover_source(con, torrent_id, url, cfg, options, budget):
    metadata, locations = source_record(con, torrent_id)
    if metadata:
        return metadata, locations
    # Optional repair cache, prepared from exact .torrent metadata. Never trust
    # cached filenames alone: verified_plan still checks every piece before use.
    if len(torrent_id) == 40 and all(c in "0123456789abcdefABCDEF" for c in torrent_id):
        cache = Path(__file__).with_name("torrent_metadata") / (torrent_id + ".json")
        if cache.is_file():
            record = json.loads(cache.read_text(encoding="utf-8"))
            store_source(con, torrent_id, record["metadata"], record["locations"])
            return record["metadata"], record["locations"]
    row = con.execute("SELECT checked_at FROM torrent_sources WHERE id=?", (torrent_id,)).fetchone()
    now = int(time.time())
    if budget[0] <= 0 or (row and now - row[0] < 86400):
        return None, []
    budget[0] -= 1
    try:
        response = requests.get(url, timeout=(10, 20))
        response.raise_for_status()
        metadata = decode_torrent(response.content)
        locations = download_locations(cfg, options)
        store_source(con, torrent_id, metadata, locations)
        return metadata, locations
    except Exception as error:
        con.execute("""INSERT INTO torrent_sources(id,checked_at,last_error) VALUES(?,?,?)
            ON CONFLICT(id) DO UPDATE SET checked_at=excluded.checked_at,last_error=excluded.last_error""",
            (torrent_id, now, str(error)))
        con.commit()
        logging.warning("Cannot recover torrent metadata %s: %s", torrent_id, error)
        return None, []


def verified_plan(metadata, locations, title, rule, saved_plan, name_builder):
    """Return a plan only if exact filenames, lengths and every piece hash match."""
    saved = json.loads(saved_plan) if saved_plan else []
    saved_by_source = {str(op["source"]): op for op in saved}
    roots = list(locations)
    for entry in metadata["files"]:
        for operation in saved:
            source = Path(operation["source"])
            relative = Path(entry["path"])
            if source.parts[-len(relative.parts):] == relative.parts:
                roots.append(str(source.parents[len(relative.parts) - 1]))
    for root in dict.fromkeys(roots):
        files = []
        plan = []
        for entry in metadata["files"]:
            source = Path(root) / entry["path"]
            target = source
            if source.suffix.lower() in MEDIA:
                target = source.with_name(name_builder(title, source.name, rule) or source.name)
                previous = saved_by_source.get(str(source))
                if previous:
                    target = Path(previous["target"])
                plan.append({"source": str(source), "target": str(target)})
            candidate = source if source.is_file() else target
            if not candidate.is_file() or candidate.stat().st_size != entry["size"]:
                break
            files.append(candidate)
        else:
            if not plan:
                return []
            if len({op["target"] for op in plan}) != len(plan):
                raise ValueError("Multiple torrent files would be renamed to the same target")
            before = [(f.stat().st_size, f.stat().st_mtime_ns) for f in files]
            expected = bytes.fromhex(metadata["pieces"])
            piece_size = metadata["piece_length"]
            digest = hashlib.sha1()
            consumed = 0
            index = 0
            valid = True
            for path in files:
                with path.open("rb") as stream:
                    while True:
                        chunk = stream.read(min(1024 * 1024, piece_size - consumed))
                        if not chunk:
                            break
                        digest.update(chunk)
                        consumed += len(chunk)
                        if consumed == piece_size:
                            if digest.digest() != expected[index:index + 20]:
                                valid = False
                                break
                            index += 20
                            consumed = 0
                            digest = hashlib.sha1()
                if not valid:
                    break
            if consumed:
                valid = valid and digest.digest() == expected[index:index + 20]
                index += 20
            after = [(f.stat().st_size, f.stat().st_mtime_ns) for f in files]
            if valid and index == len(expected) and before == after:
                return plan
    return None
