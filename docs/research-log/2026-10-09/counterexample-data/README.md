# Raw counterexample data

These are the complete initial and three final JSON snapshots and SQLite databases from the independently reproduced eight-person, eight-day coordination counterexample. The candidate is rejected pending repair. One corrupt initial coordination flag changes the result; it does not represent a legitimate treatment or a formal comparison. Both restoration routes reproduce that defect.

SQLite files are published losslessly as Base64 text. Decode, for example: `python3 -c "import base64,pathlib; p=pathlib.Path('initial.sqlite.b64'); pathlib.Path('initial.sqlite').write_bytes(base64.b64decode(p.read_bytes()))"`. Validate the decoded file against `manifest.json` before querying it. JSON files only redact private absolute paths when necessary; raw and published hashes are separate.
