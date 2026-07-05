# Vision: quotes (Python CLI)

A tiny single-file Python CLI, stdlib only, called `quotes.py`. It stores short quotes in a
local JSON file (`quotes.json`, created on first use, next to the script) and supports three
subcommands:

- `python quotes.py add "<text>" --author "<name>"` — append a quote (`--author` optional,
  defaults to `"unknown"`); print the saved quote.
- `python quotes.py list` — print every stored quote, one per line, as `"<text>" — <author>`;
  print `no quotes yet` if the file is empty or missing.
- `python quotes.py random` — print one randomly chosen quote in the same format; print
  `no quotes yet` if there are none.

Use `argparse` for subcommands and `json` for storage. No third-party dependencies, no
database, no network. Keep it to one file. Include `pytest` tests covering add, list, random,
and the empty-store case (use `tmp_path` / `monkeypatch` so tests don't touch the real
`quotes.json`).
