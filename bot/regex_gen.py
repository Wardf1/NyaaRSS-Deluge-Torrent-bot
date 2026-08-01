#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


EPISODE_PATTERNS = [
    # S02E03
    re.compile(
        r"\bS(?P<season>\d{1,2})E(?P<episode>\d{1,3})\b",
        re.IGNORECASE,
    ),

    # 2x03
    re.compile(
        r"\b(?P<season>\d{1,2})x(?P<episode>\d{1,3})\b",
        re.IGNORECASE,
    ),

    # - 05
    re.compile(
        r"\s+-\s+(?P<episode>\d{1,3})(?=\s|$|\[|\()",
        re.IGNORECASE,
    ),
]


KNOWN_RELEASE_GROUPS = {
    "toonshub",
    "erai-raws",
    "judas",
    "asw",
    "dkb",
    "varyg",
    "subsplease",
    "ember",
    "anime time",
}


SOURCE_PATTERNS = [
    ("BILI", re.compile(r"\bBILI\b", re.IGNORECASE)),
    ("DSNP", re.compile(r"\bDSNP\b", re.IGNORECASE)),
    ("NF", re.compile(r"\bNF\b", re.IGNORECASE)),
    ("CR", re.compile(r"\bCR\b", re.IGNORECASE)),
    ("AMZN", re.compile(r"\bAMZN\b", re.IGNORECASE)),
    ("WEB-DL", re.compile(r"\bWEB[- .]?DL\b", re.IGNORECASE)),
    ("WEBRip", re.compile(r"\bWEB[- .]?Rip\b", re.IGNORECASE)),
    ("BluRay", re.compile(r"\bBlu[- .]?Ray\b", re.IGNORECASE)),
]


CODEC_PATTERNS = [
    ("H.265", re.compile(r"\b(?:H[ .]?265|HEVC|x265)\b", re.IGNORECASE)),
    ("H.264", re.compile(r"\b(?:H[ .]?264|AVC|x264)\b", re.IGNORECASE)),
    ("AV1", re.compile(r"\bAV1\b", re.IGNORECASE)),
    ("VP9", re.compile(r"\bVP9\b", re.IGNORECASE)),
]


RESOLUTION_PATTERN = re.compile(
    r"\b(?P<resolution>2160p|1440p|1080p|720p|480p)\b",
    re.IGNORECASE,
)


SUBTITLE_PATTERNS = [
    ("Multi-Subs", re.compile(r"\bMulti[- ]?Subs?\b", re.IGNORECASE)),
    ("MultiSub", re.compile(r"\bMultiSub\b", re.IGNORECASE)),
]


TRAILING_METADATA_PATTERNS = [
    re.compile(r"\[[0-9A-F]{8}\]$", re.IGNORECASE),
    re.compile(r"\([^)]*Multi[- ]?Subs?[^)]*\)$", re.IGNORECASE),
    re.compile(r"\[[^]]*MultiSub[^]]*\]", re.IGNORECASE),
]


def escape_regex_fragment(value: str) -> str:
    """
    Escape text for use inside a regular expression and make spaces tolerant.

    Example:
        The Elusive Samurai

    Becomes:
        The\\s+Elusive\\s+Samurai
    """
    escaped = re.escape(value.strip())
    escaped = escaped.replace(r"\ ", r"\s+")

    return escaped


def normalize_title(value: str) -> str:
    value = value.lower()

    value = value.replace("ō", "o")
    value = value.replace("ū", "u")
    value = value.replace("ā", "a")
    value = value.replace("ī", "i")
    value = value.replace("ē", "e")

    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def similarity_key(value: str) -> str:
    """
    Create a simplified key used for title comparison.

    Common words that often differ between English and release titles
    are removed.
    """
    normalized = normalize_title(value)

    ignored = {
        "the",
        "a",
        "an",
        "after",
        "my",
        "to",
        "of",
        "in",
        "and",
        "was",
        "i",
        "get",
        "got",
    }

    words = [
        word
        for word in normalized.split()
        if word not in ignored and len(word) > 1
    ]

    return " ".join(words)


def title_similarity(first: str, second: str) -> float:
    first_words = set(similarity_key(first).split())
    second_words = set(similarity_key(second).split())

    if not first_words or not second_words:
        return 0.0

    intersection = len(first_words & second_words)
    union = len(first_words | second_words)

    return intersection / union if union else 0.0


def extract_release_group(line: str) -> Tuple[Optional[str], str]:
    match = re.match(r"^\[(?P<group>[^\]]+)]\s*", line)

    if not match:
        return None, line.strip()

    group = match.group("group").strip()
    remainder = line[match.end():].strip()

    return group, remainder


def detect_episode(
    line: str,
) -> Tuple[
    Optional[int],
    Optional[int],
    Optional[re.Match[str]],
    bool,
]:
    """
    Return:
        season,
        episode,
        regular expression match,
        whether the season must be supplied by the user
    """
    for index, pattern in enumerate(EPISODE_PATTERNS):
        match = pattern.search(line)

        if not match:
            continue

        season_value = match.groupdict().get("season")
        episode_value = match.groupdict().get("episode")

        if not episode_value:
            continue

        episode = int(episode_value)

        if season_value:
            return int(season_value), episode, match, False

        # The "- 05" format does not contain a season number.
        return None, episode, match, index == 2

    return None, None, None, False


def clean_title_candidate(value: str) -> str:
    value = value.strip(" -–—._")

    for pattern in TRAILING_METADATA_PATTERNS:
        value = pattern.sub("", value).strip()

    value = re.sub(r"\s+", " ", value)

    return value.strip(" -–—._")


def extract_title_before_episode(
    line_without_group: str,
    episode_match: Optional[re.Match[str]],
) -> str:
    if episode_match:
        candidate = line_without_group[:episode_match.start()]
    else:
        candidate = line_without_group

        metadata_match = re.search(
            r"\b(?:"
            r"2160p|1440p|1080p|720p|480p|"
            r"WEB[- .]?DL|WEB[- .]?Rip|"
            r"HEVC|H[ .]?264|H[ .]?265|"
            r"x264|x265|AVC|AV1"
            r")\b",
            candidate,
            re.IGNORECASE,
        )

        if metadata_match:
            candidate = candidate[:metadata_match.start()]

    return clean_title_candidate(candidate)


def extract_parenthesis_aliases(line: str) -> List[str]:
    aliases: List[str] = []

    for match in re.finditer(r"\(([^()]*)\)", line):
        content = match.group(1).strip()

        if not content:
            continue

        parts = [
            part.strip()
            for part in content.split(",")
        ]

        for part in parts:
            normalized = normalize_title(part)

            if not normalized:
                continue

            if re.search(
                r"\b(?:"
                r"multi subs?|aac|flac|h264|h265|hevc|1080p|720p"
                r")\b",
                normalized,
                re.IGNORECASE,
            ):
                continue

            if len(part) >= 5:
                aliases.append(
                    clean_title_candidate(part)
                )

    return aliases


def detect_resolution(line: str) -> Optional[str]:
    match = RESOLUTION_PATTERN.search(line)

    return match.group("resolution") if match else None


def detect_source_tokens(line: str) -> List[str]:
    found: List[str] = []

    for name, pattern in SOURCE_PATTERNS:
        if pattern.search(line) and name not in found:
            found.append(name)

    return found


def detect_codec(line: str) -> Optional[str]:
    for codec, pattern in CODEC_PATTERNS:
        if pattern.search(line):
            return codec

    return None


def detect_subtitles(line: str) -> Optional[str]:
    for name, pattern in SUBTITLE_PATTERNS:
        if pattern.search(line):
            return name

    return None


def should_transcode(codec: Optional[str]) -> bool:
    if codec == "H.265":
        return False

    if codec in {"H.264", "AV1", "VP9"}:
        return True

    # Safer default for an unknown codec.
    return False


def select_primary_source(
    source_tokens: List[str],
) -> Optional[str]:
    platform_priority = [
        "BILI",
        "DSNP",
        "NF",
        "CR",
        "AMZN",
    ]

    for source in platform_priority:
        if source in source_tokens:
            return source

    for source in (
        "WEB-DL",
        "WEBRip",
        "BluRay",
    ):
        if source in source_tokens:
            return source

    return None


def build_release_regex(
    group: Optional[str],
    title: str,
    resolution: Optional[str],
    source_tokens: List[str],
    codec: Optional[str],
    subtitles: Optional[str],
) -> str:
    parts: List[str] = ["^"]

    if group:
        parts.append(r"\[")
        parts.append(
            escape_regex_fragment(group)
        )
        parts.append(r"\]")
        parts.append(".*")

    parts.append(
        escape_regex_fragment(title)
    )
    parts.append(".*")

    if resolution:
        parts.append(
            re.escape(resolution)
        )
        parts.append(".*")

    primary_source = select_primary_source(
        source_tokens
    )

    if primary_source:
        if primary_source == "WEB-DL":
            parts.append(r"WEB[- .]?DL")

        elif primary_source == "WEBRip":
            parts.append(r"WEB[- .]?Rip")

        elif primary_source == "BluRay":
            parts.append(r"Blu[- .]?Ray")

        else:
            parts.append(
                re.escape(primary_source)
            )

        parts.append(".*")

    if codec == "H.265":
        parts.append(
            r"(?:H[ .]?265|HEVC|x265)"
        )
        parts.append(".*")

    elif codec == "H.264":
        parts.append(
            r"(?:H[ .]?264|AVC|x264)"
        )
        parts.append(".*")

    elif codec:
        parts.append(
            re.escape(codec)
        )
        parts.append(".*")

    if subtitles:
        if subtitles == "Multi-Subs":
            parts.append(
                r"Multi[- ]?Subs?"
            )
        else:
            parts.append(
                r"MultiSub"
            )

    while parts and parts[-1] == ".*":
        parts.pop()

    return "".join(parts)


def ask_for_season(
    title: str,
    episode: int,
    default_season: Optional[int],
) -> int:
    if default_season is not None:
        return default_season

    while True:
        try:
            raw_value = input(
                f'Series "{title}" contains an episode '
                f'written as "- {episode:02d}". '
                f"Enter the season number: "
            ).strip()

            season = int(raw_value)

            if season < 0 or season > 99:
                raise ValueError

            return season

        except ValueError:
            print(
                "Enter a valid season number "
                "between 0 and 99."
            )


def find_matching_series(
    groups: List[Dict[str, Any]],
    title: str,
    aliases: List[str],
    threshold: float,
) -> Optional[Dict[str, Any]]:
    candidates = [
        title,
        *aliases,
    ]

    best_group: Optional[Dict[str, Any]] = None
    best_score = 0.0

    for group in groups:
        known_titles = group["titles"]

        for candidate in candidates:
            for known_title in known_titles:
                score = title_similarity(
                    candidate,
                    known_title,
                )

                if score > best_score:
                    best_score = score
                    best_group = group

    if best_score >= threshold:
        return best_group

    return None


def choose_series_key(
    titles: List[str],
) -> str:
    unique_titles: List[str] = []
    seen = set()

    for title in titles:
        cleaned = clean_title_candidate(title)

        if not cleaned:
            continue

        normalized = normalize_title(cleaned)

        if normalized in seen:
            continue

        seen.add(normalized)
        unique_titles.append(cleaned)

    # Prefer an English-looking title before
    # a romanized Japanese title.
    unique_titles.sort(
        key=lambda title: (
            bool(
                re.search(
                    r"\b(?:"
                    r"wa|no|ni|wo|ga|kara|made|shita"
                    r")\b",
                    title,
                    re.IGNORECASE,
                )
            ),
            -len(title.split()),
        )
    )

    return "|".join(unique_titles)


def parse_line(
    line: str,
    default_season: Optional[int],
) -> Dict[str, Any]:
    release_group, without_group = (
        extract_release_group(line)
    )

    (
        season,
        episode,
        episode_match,
        missing_season,
    ) = detect_episode(without_group)

    title = extract_title_before_episode(
        without_group,
        episode_match,
    )

    aliases = extract_parenthesis_aliases(
        line
    )

    if missing_season and episode is not None:
        season = ask_for_season(
            title,
            episode,
            default_season,
        )

    resolution = detect_resolution(line)
    source_tokens = detect_source_tokens(line)
    codec = detect_codec(line)
    subtitles = detect_subtitles(line)

    include_regex = build_release_regex(
        group=release_group,
        title=title,
        resolution=resolution,
        source_tokens=source_tokens,
        codec=codec,
        subtitles=subtitles,
    )

    return {
        "original_line": line,
        "release_group": release_group,
        "title": title,
        "aliases": aliases,
        "season": season,
        "episode": episode,
        "resolution": resolution,
        "source_tokens": source_tokens,
        "codec": codec,
        "subtitles": subtitles,
        "include_regex": include_regex,
        "transcode": should_transcode(codec),
    }


def merge_entries(
    parsed_entries: List[Dict[str, Any]],
    similarity_threshold: float,
) -> List[Dict[str, Any]]:
    groups: List[Dict[str, Any]] = []

    for entry in parsed_entries:
        matching = find_matching_series(
            groups=groups,
            title=entry["title"],
            aliases=entry["aliases"],
            threshold=similarity_threshold,
        )

        if matching is None:
            matching = {
                "titles": [],
                "entries": [],
            }

            groups.append(matching)

        for title in [
            entry["title"],
            *entry["aliases"],
        ]:
            if (
                title
                and title not in matching["titles"]
            ):
                matching["titles"].append(title)

        matching["entries"].append(entry)

    return groups


def build_config(
    groups: List[Dict[str, Any]],
) -> Dict[str, Any]:
    series_config: Dict[str, Any] = {}

    for group in groups:
        entries = group["entries"]

        series_key = choose_series_key(
            group["titles"]
        )

        regexes: List[str] = []
        seasons: List[int] = []

        for entry in entries:
            regex = entry["include_regex"]

            if regex not in regexes:
                regexes.append(regex)

            if entry["season"] is not None:
                seasons.append(
                    int(entry["season"])
                )

        # Enable transcoding if at least one matching
        # release uses a codec other than HEVC/H.265.
        transcode = any(
            entry["transcode"]
            for entry in entries
        )

        rule: Dict[str, Any] = {
            "include_regex": regexes,
            "trusted_only": False,
            "skip_remakes": False,
            "min_seeders": 0,
            "transcode": transcode,
        }

        # Especially useful when the input contains
        # "- 05" instead of S01E05.
        if seasons and len(set(seasons)) == 1:
            rule["default_season"] = seasons[0]

        series_config[series_key] = rule

    return {
        "series": series_config,
    }


def read_input_lines(
    path: Path,
) -> List[str]:
    try:
        content = path.read_text(
            encoding="utf-8-sig"
        )

    except UnicodeDecodeError:
        content = path.read_text(
            encoding="cp1250"
        )

    return [
        line.strip()
        for line in content.splitlines()
        if (
            line.strip()
            and not line.lstrip().startswith("#")
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a basic Nyaa RSS series "
            "configuration from release names "
            "stored in a TXT file."
        )
    )

    parser.add_argument(
        "input",
        type=Path,
        help=(
            "TXT file in which each line "
            "contains one release name."
        ),
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path(
            "generated_series.json"
        ),
        help=(
            "Output JSON file. "
            "Default: generated_series.json"
        ),
    )

    parser.add_argument(
        "--season",
        type=int,
        default=None,
        help=(
            "Default season number for entries "
            "that contain only an episode number, "
            'for example "- 05".'
        ),
    )

    parser.add_argument(
        "--similarity",
        type=float,
        default=0.42,
        help=(
            "Similarity threshold used to merge "
            "different names of the same series. "
            "Default: 0.42."
        ),
    )

    args = parser.parse_args()

    if not args.input.exists():
        print(
            f"Input file not found: {args.input}",
            file=sys.stderr,
        )
        return 1

    if (
        args.season is not None
        and not 0 <= args.season <= 99
    ):
        print(
            "--season must be between 0 and 99.",
            file=sys.stderr,
        )
        return 1

    lines = read_input_lines(
        args.input
    )

    if not lines:
        print(
            "The input file does not contain "
            "any valid entries.",
            file=sys.stderr,
        )
        return 1

    parsed_entries: List[
        Dict[str, Any]
    ] = []

    for number, line in enumerate(
        lines,
        start=1,
    ):
        try:
            parsed = parse_line(
                line=line,
                default_season=args.season,
            )

            parsed_entries.append(parsed)

            season_text = (
                f"S{parsed['season']:02d}"
                f"E{parsed['episode']:02d}"
                if (
                    parsed["season"] is not None
                    and parsed["episode"] is not None
                )
                else "no complete episode number"
            )

            print(
                f"[{number}/{len(lines)}] "
                f"{parsed['title']} | "
                f"{season_text} | "
                f"{parsed['codec'] or 'unknown codec'}"
            )

        except Exception as error:
            print(
                f"Error while processing line "
                f"{number}: {error}",
                file=sys.stderr,
            )

            print(
                f"Line: {line}",
                file=sys.stderr,
            )

    grouped_entries = merge_entries(
        parsed_entries=parsed_entries,
        similarity_threshold=args.similarity,
    )

    output_config = build_config(
        grouped_entries
    )

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    args.output.write_text(
        json.dumps(
            output_config,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print(
        f"Configuration saved to: "
        f"{args.output}"
    )
    print(
        f"Number of input lines: "
        f"{len(lines)}"
    )
    print(
        f"Number of detected series: "
        f"{len(output_config['series'])}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# Wardf
