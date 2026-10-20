# Release filters and rename templates

Series keys select a title. include_regex and exclude_regex then check the release. Matching uses Python re.search with case ignored; the first matching series key wins.

The fictional HEVC example in bot/config.example.json matches:

    [ExampleGroup] Example HEVC Series - S01E03 [1080p][HEVC x265 10bit][Multi-Subs] (Weekly)

Its optional (?:v\d+)? suffix accepts revisions such as v2. Anchors \A and \Z restrict matching to the entire title, rejecting a different group, resolution, codec, or subtitle variant. Backslashes must be doubled inside JSON strings.

Rename templates support {se} (S01E03), {season}, {episode}, and {ext}. The original extension is appended when needed. Avoid filename characters that are invalid on the target filesystem. Rename happens only after the torrent has left Deluge and its files have passed recovery verification.

regex_gen.py produces candidate rules from release names. Review generated filters against actual titles before adding them to a private configuration.
