## Regex Examples

The bot uses **Python Regular Expressions** (`re.search`) to match torrent titles.

### Basic Examples

| Regex            | Matches                             | Doesn't Match    |
| ---------------- | ----------------------------------- | ---------------- |
| `One Piece`      | `One Piece - 1135`                  | `Bleach`         |
| `Dr Stone`       | `Dr Stone`                          | `Science Future` |
| `Dr\\.? ?Stone`  | `Dr Stone`, `Dr.Stone`, `Dr. Stone` | `Stone Dr`       |
| `Science Future` | `Dr Stone - Science Future`         | `Dr Stone S03`   |
| `One Piece\|OP`  | `One Piece`, `OP`                   | `Bleach`         |

---

### Release Group Examples

| Regex                        | Matches                         |
| ---------------------------- | ------------------------------- |
| `^\\[SubsPlease\\]`          | `[SubsPlease] One Piece...`     |
| `^\\[Judas\\]`               | `[Judas] Dr Stone...`           |
| `^\\[Erai-raws\\]`           | `[Erai-raws] Bleach...`         |
| `^\\[(SubsPlease\|Judas)\\]` | `[SubsPlease]...`, `[Judas]...` |

---

### Resolution Examples

| Regex         | Matches                                                         |
| ------------- | --------------------------------------------------------------- |
| `1080p`       | Any title containing `1080p`                                    |
| `2160p`       | Any title containing `2160p`                                    |
| `^(?!.*720p)` | Titles not containing `720p` *(better used in `exclude_regex`)* |

---

### Codec Examples

| Regex     | Matches        |
| --------- | -------------- |
| `HEVC`    | HEVC releases  |
| `x265`    | x265 releases  |
| `AV1`     | AV1 releases   |
| `H\\.264` | H.264 releases |

---

### Audio Examples

| Regex         | Matches                   |
| ------------- | ------------------------- |
| `Dual-Audio`  | Dual audio releases       |
| `Multi-Subs`  | Multi-subtitle releases   |
| `English-Sub` | English subtitle releases |

---

### Episode Examples

| Regex      | Matches                                          |
| ---------- | ------------------------------------------------ |
| `S01E12`   | Season 1 Episode 12                              |
| `- 12 `    | Episode 12 in releases like `Anime - 12 (1080p)` |
| `\\b12\\b` | Standalone episode number `12`                   |

---

### Combining Conditions

Match a Judas HEVC 1080p release:

```regex
^\\[Judas\\].*1080p.*HEVC
```

Match either SubsPlease or Erai-raws:

```regex
^\\[(SubsPlease|Erai-raws)\\]
```

Match Dr Stone or Science Future:

```regex
Dr\\.? ?Stone|Science Future
```

---

### Common `exclude_regex`

```json
[
  "480p",
  "720p",
  "Batch",
  "VOSTFR",
  "RAW",
  "CAM"
]
```

---

### Tips

* `^` = start of title
* `$` = end of title
* `.` = any character
* `.*` = any text
* `|` = OR
* `()` = group expressions
* `[]` = character class (must be escaped when matching literal `[` or `]`)
* `\\.` = literal dot (`.`)
* `\\?` = previous character is optional

**Example:**

```regex
Dr\\.? ?Stone
```

matches all of these:

* Dr Stone
* Dr.Stone
* Dr. Stone

while

```regex
^\\[Judas\\].*1080p.*HEVC
```

matches titles like:

```
[Judas] Dr Stone - Science Future - S04E37 [1080p][HEVC x265 10bit][Dual-Audio][Multi-Subs]
```
