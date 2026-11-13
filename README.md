# osaeru

>The Japanese verb osaeru (おさえる) means "to hold down," "to restrain," >"to suppress," or "to grasp/comprehend"

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

Open the **Settings** tab, paste your key and press Save. The app checks the
key against the API before saving it (if you're offline it saves anyway and
says so). On desktop the key goes in the OS keychain via `keyring`; on iOS,
where `keyring` has no backend, it goes in a private file in the app's
sandboxed data directory. It is never written to the database or a config file.

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
- `flowchart.py` uses a simple circular layout - fine for a few dozen
  skills, will get messy well beyond that.
- No conflict handling if you edit the same session note from two devices
  (not a concern for a single-iPad personal app, but worth knowing).

## Version control

This directory is a normal Python project - `git init` and commit as-is.
The `.gitignore` already excludes the local SQLite file, virtualenvs, and
Briefcase build artefacts, so nothing personal or generated gets checked in.

## Web front end

`src/osaeru/web/` is a browser front end built on the same `database.py` and
`claude_sync.py`, without changing them. It runs anywhere a browser does,
including Safari on an iPad (Add to Home Screen opens it full-screen).

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m osaeru.web  
```

It uses the same SQLite file and keychain key as the desktop app.
`OSAERU_DB_PATH` points it at a different database, `OSAERU_RELOAD=1`
auto-reloads while developing, and `PORT` / `OSAERU_HOST` change where it
listens. It has no login, so it only listens on localhost by default; don't
expose it on a shared network.

Tests: `pytest`.
