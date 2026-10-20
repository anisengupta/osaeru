# osaeru

![alt text](docs/image.png)

A personal, mostly-offline jiu jitsu training companion for iPad. Runs
entirely on-device (SQLite + local notifications); only calls out to the
Claude API when there's a connection, to parse session notes into a skills
flowchart, chat about training, and generate recommendations.

## Project layout

```
src/osaeru/
  app.py              # Toga app entry point, tab navigation
  database.py         # SQLite schema + all local read/write
  claude_sync.py       # The only module that talks to the internet
  notifications.py     # Local notification stub - see docstring, needs
                        # verification against current Toga/iOS API
  pages/
    profile.py
    notes.py
    flowchart.py
    chat.py
    recommendations.py
```

## Getting started (desktop dev loop)

```bash
python -m venv .venv
source .venv/bin/activate
pip install briefcase toga anthropic keyring

briefcase dev
```

This runs the app as a normal desktop window so you can iterate fast before
touching iOS/Xcode at all.

## Setting your Claude API key

The app reads the key from the OS keychain via `keyring`, never from a
config file or the database. For now, set it once from a Python shell in
your dev environment:

```python
from osaeru import claude_sync
claude_sync.set_api_key("sk-ant-...")
```

Eventually this should move into a small Settings screen in the app itself
so you're not doing it from a REPL.

## Building for iOS / your iPad

Requires Xcode installed, with your Apple ID added under
Xcode > Settings > Accounts.

```bash
briefcase create iOS
briefcase build iOS
briefcase run iOS      # deploys to a connected iPad or the simulator
```

With a free Apple ID, the app needs re-signing/reinstalling roughly every 7
days. A $99/year Apple Developer account removes that limit.

## Known gaps to close before this is daily-usable

- `notifications.py` is a stub - the local notification API needs
  confirming against whatever Toga/Briefcase version you land on.
- No Settings screen yet for entering the API key from within the app.
- `flowchart.py` uses a simple circular layout - fine for a few dozen
  skills, will get messy well beyond that.
- No conflict handling if you edit the same session note from two devices
  (not a concern for a single-iPad personal app, but worth knowing).

## Version control

This directory is a normal Python project - `git init` and commit as-is.
The `.gitignore` already excludes the local SQLite file, virtualenvs, and
Briefcase build artefacts, so nothing personal or generated gets checked in.
