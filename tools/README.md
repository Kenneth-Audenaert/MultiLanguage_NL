# MultiLanguage_NL Tools

Translation pipeline for generating Dutch language data for the MultiLanguage_NL addon.

## Architecture

```
precache.py  ──▶  FastAPI server  ──▶  Opus MT (EN→NL)
                      │
                      ▼
               MultiLanguage_NL/Database/*.lua
```

1. **precache.py** parses the English source data from the MultiLanguage addon
2. Sends texts in batches to the translation server
3. Generates MultiLanguage-compatible Lua files for the NL language pack
4. Optionally generates TTS audio (OGG) for quest texts via Piper

## Quick start

### 1. Start the translation server

```bash
cd tools/server
docker build -t mlnl-server .
docker run -p 8000:8000 mlnl-server
```

The first build downloads and converts the Opus MT model (~500 MB). Subsequent builds use the Docker cache.

Verify the server is running:
```bash
curl http://localhost:8000/health
```

### 2. Run translations

```bash
cd tools
pip install -r requirements.txt

# Translate quests (default)
python precache.py

# Translate specific data types
python precache.py --type items
python precache.py --type npcs
python precache.py --type spells
python precache.py --type all

# Translate quests with TTS audio
python precache.py --type quests --tts

# Generate OGG audio files for the addon
python precache.py --tts-ogg

# More parallel workers for faster translation
python precache.py --type items --workers 8

# Dry run (count tasks without translating)
python precache.py --dry-run

# Regenerate Lua files from existing translations
python precache.py --generate-only
```

Progress is saved to `precache_progress_*.json` — interrupted runs resume automatically.

## Configuration

Configuration via environment variables (all optional):

| Variable | Default | Description |
|---|---|---|
| `MLNL_SERVER_URL` | `http://localhost:8000` | Translation server URL |
| `MLNL_ADDONS_DIR` | *(auto-detected)* | WoW AddOns directory. Auto-detects common install paths. |
| `MLNL_NAS_SHARE` | *(empty)* | Optional NAS share for `--output nas` |

## Server API

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Server status and model info |
| `/stats` | GET | Audio cache statistics |
| `/translate` | POST | Translate a single text |
| `/translate/batch` | POST | Translate multiple texts (preferred) |
| `/tts` | POST | Generate TTS audio |
| `/tts/ogg` | POST | Generate TTS audio in OGG format |
| `/glossary` | GET/POST/DELETE | Manage WoW-specific translation glossary |

## Requirements

- **precache.py**: Python 3.10+, `requests`
- **server**: Docker, or Python 3.12+ with dependencies from `server/requirements.txt`
