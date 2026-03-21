# MultiLanguage_NL

Dutch (NL) language pack for the [MultiLanguage](https://github.com/rubenzantingh/MultiLanguage) addon by [rubenzantingh](https://github.com/rubenzantingh).

## Description

This addon adds Dutch (Nederlands) translations for World of Warcraft quests, items, NPCs, and spells to the MultiLanguage addon. It also includes optional Text-to-Speech (TTS) support that plays pre-generated Dutch audio for quest texts.

## Requirements

- [MultiLanguage](https://www.curseforge.com/wow/addons/multilanguage) addon installed

## Installation

1. Download the latest release and extract it into your WoW AddOns folder:
   ```
   World of Warcraft/_retail_/Interface/AddOns/MultiLanguage_NL/
   ```
2. Make sure the `MultiLanguage` addon is also installed.
3. In-game, open MultiLanguage settings and select **Dutch** as your language.

## Features

- **Quest translations** — Full Dutch translations for quest descriptions, objectives, and completion text
- **Item translations** — Dutch names and descriptions for items
- **NPC translations** — Dutch NPC names and roles
- **Spell translations** — Dutch spell names and descriptions
- **TTS support** — Optional Text-to-Speech playback for quest texts (requires pre-generated audio files)

## Slash commands

| Command | Description |
|---|---|
| `/mlnl` | Show loaded translation counts per data type |

## TTS Audio (optional)

TTS audio files are not included in this repository due to their size (~3.7 GB). They can be generated using the `precache.py` tool with the `--tts-ogg` flag. Place the resulting `.ogg` files in the `Audio/` folder.

## Translation pipeline

Translations are machine-generated using [Opus MT](https://github.com/Helsinki-NLP/Opus-MT) (English to Dutch) and refined with a WoW-specific glossary. The `precache.py` tool handles batch translation and Lua file generation.

## Credits

- [rubenzantingh](https://github.com/rubenzantingh) — Original MultiLanguage addon
- [Helsinki-NLP](https://github.com/Helsinki-NLP/Opus-MT) — Machine translation model
