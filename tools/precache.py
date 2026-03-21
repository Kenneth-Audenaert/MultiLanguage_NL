"""
MultiLanguage_NL Pre-Cache Script
Parst de MultiLanguage databases en vertaalt alle Engelse teksten
via de MultiLanguage_NL FastAPI server.

Genereert MultiLanguage-compatibele Lua bestanden voor de NL taalpack,
zodat vertalingen direct beschikbaar zijn in-game.

Gebruik:
    python precache.py                          # vertaal quests (default)
    python precache.py --type items             # vertaal items
    python precache.py --type npcs              # vertaal NPCs
    python precache.py --type spells            # vertaal spells
    python precache.py --type all               # vertaal alles
    python precache.py --type quests --tts      # quests met TTS audio erbij
    python precache.py --tts-only               # TTS voor reeds vertaalde quests
    python precache.py --tts-ogg                # OGG audio naar addon Audio map
    python precache.py --workers 8              # meer parallelle verzoeken
    python precache.py --dry-run                # tel taken zonder te vertalen
    python precache.py --generate-only          # genereer Lua uit bestaande voortgang
"""

import os
import re
import sys
import json
import time
import signal
import argparse
import logging
import requests
from pathlib import Path
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("ML_NL-PreCache")

# ─── Configuratie ────────────────────────────────────────────────────────────
# Overschrijf via environment variables of pas de defaults hieronder aan.
SERVER_URL = os.environ.get("MLNL_SERVER_URL", "http://localhost:8000")

def _detect_addons_dir() -> str:
    """Auto-detect WoW AddOns directory, or fall back to script location."""
    candidates = [
        Path(__file__).resolve().parents[1],  # tools/ zit in de addon dir
        Path(r"C:\Program Files (x86)\World of Warcraft\_retail_\Interface\AddOns"),
        Path(r"C:\Program Files\World of Warcraft\_retail_\Interface\AddOns"),
        Path(r"D:\Games\World of Warcraft\_retail_\Interface\AddOns"),
        Path(r"E:\Games\World of Warcraft\_retail_\Interface\AddOns"),
    ]
    for p in candidates:
        if (p / "MultiLanguage").is_dir():
            return str(p)
    return str(candidates[0])

ADDONS_DIR = Path(
    os.environ.get("MLNL_ADDONS_DIR", _detect_addons_dir())
)
ML_DIR = ADDONS_DIR / "MultiLanguage"
NL_DIR = ADDONS_DIR / "MultiLanguage_NL"
SCRIPT_DIR = Path(__file__).parent

# Optionele NAS share — indien gezet, schrijft precache ook hierheen
_nas = os.environ.get("MLNL_NAS_SHARE", "")
NAS_SHARE = Path(_nas) if _nas else None
NAS_NL_DIR = NAS_SHARE  # Database/ en Audio/ staan direct in shared/

# WoW placeholder tags: <name>, <class>, <race> etc.
WOW_TAGS_RE = re.compile(r"<(name|class|race|gender|faction)>", re.IGNORECASE)

# Regex voor Lua data regels en velden
FIELD_RE = re.compile(
    r'(\w+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|nil)'
)

# Nummer woorden voor bestandssplitsing
FILE_NUMBERS = [
    "One", "Two", "Three", "Four", "Five",
    "Six", "Seven", "Eight", "Nine", "Ten",
]


# ─── Versioning ──────────────────────────────────────────────────────────────
VERSION_FILE = SCRIPT_DIR / "precache_versions.json"
RETRANSLATE_TRACKER = SCRIPT_DIR / "precache_retranslate_tracker.json"


def load_versions() -> dict:
    """Laad versie manifest."""
    if VERSION_FILE.exists():
        try:
            return json.loads(VERSION_FILE.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"current_version": 0, "quest_versions": {}, "tts_versions": {}, "runs": []}


def save_versions(data: dict):
    """Sla versie manifest op."""
    VERSION_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def bump_version(versions: dict, run_type: str, scope: str,
                 description: str, stats: dict | None = None) -> int:
    """Verhoog versie en registreer de run. Geeft nieuwe versie terug."""
    new_ver = versions["current_version"] + 1
    versions["current_version"] = new_ver
    versions["runs"].append({
        "version": new_ver,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": run_type,
        "scope": scope,
        "description": description,
        "completed": False,
        "stats": stats or {},
    })
    return new_ver


def resume_or_bump(versions: dict, run_type: str, scope: str,
                   description: str) -> tuple[int, bool]:
    """Hervat een onafgeronde run met dezelfde description, of maak een nieuwe.

    Returns: (version, is_resume)
    """
    if versions["runs"]:
        last_run = versions["runs"][-1]
        if (not last_run.get("completed", True)
                and last_run.get("description") == description
                and last_run.get("type") == run_type):
            log.info(f"Hervatting van onafgeronde run v{last_run['version']}: {description}")
            return last_run["version"], True

    return bump_version(versions, run_type, scope, description), False


def mark_run_completed(versions: dict):
    """Markeer de laatste run als afgerond."""
    if versions["runs"]:
        versions["runs"][-1]["completed"] = True


def stamp_quest_versions(versions: dict, quest_ids: list[int],
                         version: int, category: str = "translation"):
    """Markeer specifieke quests met een nieuwe versie."""
    key = "quest_versions" if category == "translation" else "tts_versions"
    for qid in quest_ids:
        versions[key][str(qid)] = version


def get_quest_version(versions: dict, quest_id: int,
                      category: str = "translation") -> int:
    """Haal versie op voor een specifieke quest. Default = 1."""
    key = "quest_versions" if category == "translation" else "tts_versions"
    return versions.get(key, {}).get(str(quest_id), 1)


# ─── Retranslate tracker ────────────────────────────────────────────────────
def load_retranslate_tracker() -> dict:
    """Laad tracker voor hervertaal-runs: welke keys zijn al klaar."""
    if RETRANSLATE_TRACKER.exists():
        try:
            return json.loads(RETRANSLATE_TRACKER.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 0, "done_keys": []}


def save_retranslate_tracker(data: dict):
    RETRANSLATE_TRACKER.write_text(
        json.dumps(data, ensure_ascii=False),
        encoding="utf-8",
    )


def clear_retranslate_tracker():
    if RETRANSLATE_TRACKER.exists():
        RETRANSLATE_TRACKER.unlink()


def print_version_info(versions: dict):
    """Toon versie informatie."""
    log.info(f"═══ MultiLanguage_NL Versie Informatie ═══")
    log.info(f"Huidige versie: v{versions['current_version']}")
    log.info(f"Totaal runs: {len(versions['runs'])}")

    # Per-quest versie statistieken
    qv = versions.get("quest_versions", {})
    tv = versions.get("tts_versions", {})

    if qv:
        ver_counts: dict[int, int] = {}
        for v in qv.values():
            ver_counts[v] = ver_counts.get(v, 0) + 1
        log.info(f"Quests met aangepaste vertaal-versie: {len(qv)}")
        for v in sorted(ver_counts):
            log.info(f"  v{v}: {ver_counts[v]} quests")

    if tv:
        log.info(f"Quests met aangepaste TTS-versie: {len(tv)}")

    log.info(f"")
    log.info(f"Run historie:")
    for run in versions["runs"]:
        log.info(
            f"  v{run['version']} | {run['timestamp'][:19]} | "
            f"{run['type']} | {run['scope']} | {run['description']}"
        )
        if run.get("stats"):
            for k, v in run["stats"].items():
                log.info(f"    {k}: {v}")


# ─── Data type configuratie ──────────────────────────────────────────────────
# Output doel: standaard lokaal, kan naar NAS via --output nas
OUTPUT_TARGET: Path = NL_DIR


@dataclass
class DataTypeConfig:
    name: str                # quests, items, npcs, spells
    table_name: str          # MultiLanguageQuestData etc.
    source_subdir: str       # Quests, Items, Npcs, Spells
    base_filename: str       # quests, items, npcs, spells
    num_data_files: int      # aantal genummerde data bestanden (0 = enkel bestand)
    fields: list[str]        # velden om te vertalen
    all_fields: list[str]    # alle velden in Lua output (incl. nil-only)
    id_prefix: str           # prefix voor server request ID
    supports_tts: bool       # TTS is alleen zinvol voor quests

    @property
    def source_dir(self) -> Path:
        return ML_DIR / "Database" / self.source_subdir

    @property
    def output_dir(self) -> Path:
        return OUTPUT_TARGET / "Database" / self.source_subdir

    @property
    def progress_file(self) -> Path:
        if self.name == "quests":
            return SCRIPT_DIR / "precache_progress.json"
        return SCRIPT_DIR / f"precache_progress_{self.name}.json"

    @property
    def line_re(self) -> re.Pattern:
        return re.compile(
            rf"{self.table_name}\['en'\]\[(\d+)\]\s*=\s*\{{(.+)\}}"
        )

    def source_files(self) -> list[str]:
        """Geeft lijst van bronbestanden terug (init + data bestanden)."""
        files = [f"{self.base_filename}.lua"]
        for i in range(self.num_data_files):
            files.append(f"{self.base_filename}{FILE_NUMBERS[i]}.lua")
        return files

    def data_files(self) -> list[str]:
        """Alleen de bestanden met echte data (zonder init bestand)."""
        if self.num_data_files == 0:
            # Enkel bestand: data zit in het hoofdbestand (quests)
            return [f"{self.base_filename}.lua"]
        return [f"{self.base_filename}{FILE_NUMBERS[i]}.lua"
                for i in range(self.num_data_files)]


DATA_TYPES: dict[str, DataTypeConfig] = {
    "quests": DataTypeConfig(
        name="quests",
        table_name="MultiLanguageQuestData",
        source_subdir="Quests",
        base_filename="quests",
        num_data_files=0,
        fields=["title", "description", "objective", "progress", "completion", "rewards"],
        all_fields=["title", "objective", "description", "progress", "completion", "rewards"],
        id_prefix="q",
        supports_tts=True,
    ),
    "items": DataTypeConfig(
        name="items",
        table_name="MultiLanguageItemData",
        source_subdir="Items",
        base_filename="items",
        num_data_files=4,
        fields=["name", "additional_info"],
        all_fields=["name", "additional_info"],
        id_prefix="i",
        supports_tts=False,
    ),
    "npcs": DataTypeConfig(
        name="npcs",
        table_name="MultiLanguageNpcData",
        source_subdir="Npcs",
        base_filename="npcs",
        num_data_files=5,
        fields=["name", "subname"],
        all_fields=["name", "subname"],
        id_prefix="n",
        supports_tts=False,
    ),
    "spells": DataTypeConfig(
        name="spells",
        table_name="MultiLanguageSpellData",
        source_subdir="Spells",
        base_filename="spells",
        num_data_files=8,
        fields=["name", "additional_info"],
        all_fields=["name", "additional_info"],
        id_prefix="s",
        supports_tts=False,
    ),
}


# ─── Lua helpers ─────────────────────────────────────────────────────────────
def unescape_lua(s: str) -> str:
    """Lua string escapes omzetten naar Python."""
    return (
        s.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def escape_lua_string(s: str) -> str:
    """Escape een string voor in een Lua literal."""
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace("\t", "\\t")
    )


# Alle patronen die beschermd moeten worden bij vertaling
# Volgorde is belangrijk: langste/specifiekste eerst
PROTECT_PATTERNS = [
    # HTML entities voor WoW tags: &lt;name&gt;, &lt;class&gt;, etc.
    (re.compile(r'&lt;([^&]+?)&gt;'), "HTMLENT"),
    # Gender constructies: <ein geschlagener Mann/eine geschlagene Frau>
    (re.compile(r'<[^>]*?/[^>]*?>'), "GENDER"),
    # WoW tags: <name>, <class>, <race>, <gender>, <faction>
    (re.compile(r'<(name|class|race|gender|faction)>', re.IGNORECASE), "WOWTAG"),
    # WoW kleurcodes: |cFFFFFFFF...|r
    (re.compile(r'\|c[0-9a-fA-F]{8}'), "COLSTART"),
    (re.compile(r'\|r'), "COLEND"),
    # WoW hyperlinks: |Hitem:...|h[...]|h
    (re.compile(r'\|H[^|]+\|h'), "HLINK"),
    (re.compile(r'\|h'), "HLINKEND"),
    # Items in brackets: [Monster - Black Temple - Sword, ...]
    (re.compile(r'\[[^\]]+\]'), "BRACKET"),
    # Tekst variabelen: {name}, {class}, etc.
    (re.compile(r'\{[^}]+\}'), "TVAR"),
    # Emotes/acties: <Thorius sobs.>, <Cyrus's eyes form...>
    # Begint met hoofdletter, minstens 4 tekens — WoW tags (<name> etc.) zijn al afgevangen hierboven
    (re.compile(r'<([A-Z][^>]{3,})>'), "EMOTE"),
    # HTML tags (korte, echte tags): <br>, <p>, <HTML> etc. — strippen
    (re.compile(r'</?[a-zA-Z][a-zA-Z0-9]*[^>]*>'), "HTMLTAG"),
]


def prepare_for_translation(s: str) -> tuple[str, dict[str, str]]:
    """Bereid tekst voor op vertaling: bescherm speciale content met tokens.

    Returns:
        (cleaned_text, restore_map) — restore_map bevat token → origineel
    """
    restore: dict[str, str] = {}

    def make_token(prefix: str, original: str) -> str:
        idx = len(restore)
        token = f"PROT{prefix}{idx}PROT"
        restore[token] = original
        return token

    # Bescherm alle speciale patronen
    for pattern, prefix in PROTECT_PATTERNS:
        if prefix == "HTMLTAG":
            # HTML tags strippen (niet beschermen, gewoon verwijderen)
            s = pattern.sub("", s)
        else:
            s = pattern.sub(lambda m: make_token(prefix, m.group(0)), s)

    # &nbsp; omzetten naar spatie (na entity bescherming)
    s = s.replace("&nbsp;", " ")

    return s.strip(), restore


def restore_after_translation(s: str, restore: dict[str, str]) -> str:
    """Herstel beschermde tokens in de vertaalde tekst."""
    for token, original in restore.items():
        s = s.replace(token, original)
    # Opschonen: dubbele spaties rond herstelde tokens
    s = re.sub(r'  +', ' ', s)
    return s.strip()


# ─── Generieke Lua parser ───────────────────────────────────────────────────
def parse_data(config: DataTypeConfig) -> tuple[dict[int, dict[str, str]], dict[str, list[int]]]:
    """
    Parse alle bronbestanden voor een data type.
    Returns:
        entries: {id: {field: value}}
        file_ids: {filename: [id, id, ...]}  — welke IDs uit welk bestand komen
    """
    entries: dict[int, dict[str, str]] = {}
    file_ids: dict[str, list[int]] = {}
    line_re = config.line_re

    for filename in config.source_files():
        path = config.source_dir / filename
        if not path.exists():
            log.warning(f"Bronbestand niet gevonden: {path}")
            continue

        ids_in_file = []
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = line_re.match(line.strip())
                if not m:
                    continue
                entry_id = int(m.group(1))
                fields: dict[str, str] = {}
                for fm in FIELD_RE.finditer(m.group(2)):
                    key = fm.group(1)
                    val = fm.group(2)
                    if val is not None:
                        fields[key] = unescape_lua(val)
                if fields:
                    entries[entry_id] = fields
                    ids_in_file.append(entry_id)

        if ids_in_file:
            file_ids[filename] = ids_in_file
            log.info(f"  {filename}: {len(ids_in_file)} entries")

    log.info(f"Totaal geparsed ({config.name}): {len(entries)} entries")
    return entries, file_ids


# ─── Voortgang opslaan / laden ───────────────────────────────────────────────
def load_progress(config: DataTypeConfig) -> dict[str, str]:
    """Laad eerder vertaalde resultaten {compound_key: nl_text}."""
    pf = config.progress_file
    if pf.exists():
        try:
            return json.loads(pf.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_progress(config: DataTypeConfig, data: dict[str, str]):
    """Sla tussentijdse resultaten op."""
    config.progress_file.write_text(
        json.dumps(data, ensure_ascii=False),
        encoding="utf-8",
    )


# ─── Vertaalverzoek ─────────────────────────────────────────────────────────
def _translate_batch(items: list[dict]) -> dict[str, str]:
    """Vertaal meerdere teksten in één request via /translate/batch.

    items: [{"id": "q1_p0", "text": "..."}, ...]
    Returns: {id: nl_text}
    """
    try:
        r = requests.post(
            f"{SERVER_URL}/translate/batch",
            json={"items": items},
            timeout=300,
        )
        if r.status_code == 200:
            return {
                res["id"]: res["nl"]
                for res in r.json().get("results", [])
                if res.get("nl")
            }
        log.warning(f"Server {r.status_code} voor batch ({len(items)} items)")
    except requests.RequestException as e:
        log.error(f"Verbindingsfout batch: {e}")
    return {}


def _translate_single(req_id: str, text: str) -> str | None:
    """Fallback: vertaal één tekst via /translate."""
    try:
        r = requests.post(
            f"{SERVER_URL}/translate",
            json={"id": req_id, "text": text, "tts": False},
            timeout=120,
        )
        if r.status_code == 200:
            return r.json().get("nl")
    except requests.RequestException:
        pass
    return None


def translate_text(config: DataTypeConfig, entry_id: int, field_name: str,
                   text: str, tts: bool) -> dict | None:
    """Vertaal tekst met behoud van \\n structuur en speciale tokens."""
    req_id = f"{config.id_prefix}{entry_id}_{field_name}"

    # Stap 1: bescherm speciale content
    prepared, restore = prepare_for_translation(text)

    # Stap 2: splits op \n om paragraafstructuur te behouden
    paragraphs = prepared.split("\n")

    # Stap 3: verzamel non-empty paragrafen voor batch vertaling
    batch_items = []
    para_indices = []  # index → positie in paragraphs
    for i, para in enumerate(paragraphs):
        stripped = para.strip()
        if stripped:
            batch_items.append({"id": f"{req_id}_p{i}", "text": stripped})
            para_indices.append(i)

    if not batch_items:
        log.warning(f"Geen vertaalbare tekst na voorbereiding: {req_id} (origineel: {text[:80]!r})")
        return None

    # Stap 4: vertaal alle paragrafen in één batch request
    translations = _translate_batch(batch_items)

    # Fallback naar enkel-request als batch endpoint niet beschikbaar
    if not translations and len(batch_items) > 0:
        log.warning(f"Batch mislukt voor {req_id}, fallback naar enkel-requests")
        for item in batch_items:
            nl = _translate_single(item["id"], item["text"])
            if nl:
                translations[item["id"]] = nl

    if not translations:
        log.error(f"Vertaling volledig mislukt: {req_id} ({len(batch_items)} paragrafen)")
        return None

    # Stap 5: bouw resultaat op met originele \n structuur
    translated_paragraphs = []
    for i, para in enumerate(paragraphs):
        stripped = para.strip()
        if not stripped:
            translated_paragraphs.append("")
        else:
            batch_key = f"{req_id}_p{i}"
            nl = translations.get(batch_key, stripped)
            translated_paragraphs.append(nl)

    # Stap 6: voeg samen en herstel tokens
    nl_text = "\n".join(translated_paragraphs)
    nl_text = restore_after_translation(nl_text, restore)

    if not nl_text.strip():
        log.warning(f"Lege vertaling na restore: {req_id}")
        return None

    return {"id": req_id, "nl": nl_text}


def tts_request(config: DataTypeConfig, entry_id: int,
                nl_text: str) -> dict | None:
    """Stuur één TTS verzoek naar de server (geen vertaling)."""
    req_id = f"{config.id_prefix}{entry_id}"
    try:
        r = requests.post(
            f"{SERVER_URL}/tts",
            json={"id": req_id, "text": nl_text},
            timeout=120,
        )
        if r.status_code == 200:
            return r.json()
        log.warning(f"Server {r.status_code} voor TTS {req_id}")
    except requests.RequestException as e:
        log.error(f"Verbindingsfout TTS {req_id}: {e}")
    return None


# ─── Takenlijst opbouwen ────────────────────────────────────────────────────
def build_tasks(
    config: DataTypeConfig,
    entries: dict[int, dict[str, str]],
    done: dict[str, str],
    retranslate_ids: set[int] | None = None,
    retranslate_all: bool = False,
    already_retranslated: set[str] | None = None,
) -> list[dict]:
    """Bouw lijst van te vertalen taken.

    Normaal: sla reeds vertaalde entries over (compound_key in done).
    retranslate_ids: alleen deze IDs opnieuw vertalen.
    retranslate_all: ALLE entries opnieuw vertalen.
    already_retranslated: keys die al klaar zijn in deze run (voor hervatting).
    """
    tasks = []
    skip_done = already_retranslated or set()

    for entry_id, fields in entries.items():
        # Filter op specifieke IDs als opgegeven
        if retranslate_ids and entry_id not in retranslate_ids:
            continue

        for field_name in config.fields:
            raw = fields.get(field_name)
            if not raw or not raw.strip():
                continue

            compound_key = f"{entry_id}_{field_name}"

            if retranslate_all or retranslate_ids:
                # Hervertaling: sla over als al klaar in deze run
                if compound_key in skip_done:
                    continue
            else:
                # Normale modus: sla over als al vertaald
                if compound_key in done:
                    continue

            tasks.append({
                "entry_id": entry_id,
                "field": field_name,
                "text": raw,
                "compound_key": compound_key,
            })
    return tasks


# ─── Lua bestand genereren ───────────────────────────────────────────────────
def generate_lua(config: DataTypeConfig, done: dict[str, str],
                 file_ids: dict[str, list[int]]):
    """Genereer Lua bestanden in MultiLanguage formaat."""
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Versie info voor Lua header
    versions = load_versions()
    current_ver = versions.get("current_version", 1)

    # Groepeer vertalingen per entry ID
    translated: dict[int, dict[str, str]] = {}
    for compound_key, nl_text in done.items():
        parts = compound_key.split("_", 1)
        if len(parts) != 2:
            continue
        try:
            entry_id = int(parts[0])
        except ValueError:
            continue
        if entry_id not in translated:
            translated[entry_id] = {}
        translated[entry_id][parts[1]] = nl_text

    total_entries = len(translated)
    log.info(f"Genereren ({config.name}): {total_entries} entries")

    def format_entry(entry_id: int) -> str | None:
        """Formatteer één entry als Lua regel."""
        fields = translated.get(entry_id)
        if not fields:
            return None
        parts = []
        for field_name in config.all_fields:
            val = fields.get(field_name)
            if val:
                parts.append(f'{field_name} = "{escape_lua_string(val)}"')
            else:
                parts.append(f"{field_name} = nil")
        return (
            f"{config.table_name}['nl'][{entry_id}] = "
            f"{{{', '.join(parts)}}}"
        )

    if config.num_data_files == 0:
        # Enkel bestand (quests): alles in één bestand
        lines = [
            f"{config.table_name}['nl'] = {{}}",
            "",
        ]
        for entry_id in sorted(translated.keys()):
            entry_line = format_entry(entry_id)
            if entry_line:
                lines.append(entry_line)

        out_path = output_dir / f"{config.base_filename}.lua"
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        size_mb = out_path.stat().st_size / (1024 * 1024)
        log.info(f"  {out_path.name}: {total_entries} entries, {size_mb:.1f} MB")
    else:
        # Meerdere bestanden: init bestand + gesplitste data bestanden
        # Init bestand
        init_path = output_dir / f"{config.base_filename}.lua"
        init_lines = [
            f"{config.table_name}['nl'] = {{}}",
        ]
        init_path.write_text("\n".join(init_lines) + "\n", encoding="utf-8")

        # Data bestanden: volg dezelfde ID-verdeling als de EN bronbestanden
        total_written = 0
        for filename in config.data_files():
            source_ids = file_ids.get(filename, [])
            if not source_ids:
                continue

            out_path = output_dir / filename
            lines = []
            file_count = 0
            for entry_id in source_ids:
                entry_line = format_entry(entry_id)
                if entry_line:
                    lines.append(entry_line)
                    file_count += 1

            if lines:
                out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                size_mb = out_path.stat().st_size / (1024 * 1024)
                log.info(f"  {out_path.name}: {file_count} entries, {size_mb:.1f} MB")
                total_written += file_count

        log.info(f"Totaal geschreven ({config.name}): {total_written} entries")


# ─── TTS-only modus ──────────────────────────────────────────────────────────
def process_tts_only(config: DataTypeConfig, args) -> bool:
    """Genereer TTS audio voor reeds vertaalde teksten."""
    log.info(f"═══ {config.name.upper()} (TTS-only) ═══")

    if not config.supports_tts:
        log.info(f"TTS wordt niet ondersteund voor {config.name}, overgeslagen")
        return True

    done = load_progress(config)
    if not done:
        log.warning(f"Geen vertalingen gevonden voor {config.name}")
        return True

    # Groepeer vertalingen per entry ID
    grouped: dict[int, dict[str, str]] = {}
    for compound_key, nl_text in done.items():
        parts = compound_key.split("_", 1)
        if len(parts) != 2:
            continue
        try:
            entry_id = int(parts[0])
        except ValueError:
            continue
        if entry_id not in grouped:
            grouped[entry_id] = {}
        grouped[entry_id][parts[1]] = nl_text

    # Bouw TTS taken: combineer description als primaire tekst
    tts_tasks = []
    for entry_id, fields in grouped.items():
        # Gebruik description als hoofd-TTS tekst, fallback naar andere velden
        text = fields.get("description", "")
        if not text.strip():
            for fallback in ["completion", "objective", "title"]:
                text = fields.get(fallback, "")
                if text.strip():
                    break
        if not text.strip():
            continue
        tts_tasks.append({"entry_id": entry_id, "text": text})

    log.info(f"TTS taken: {len(tts_tasks)} quests met tekst")

    if args.dry_run:
        return True

    # Graceful shutdown
    shutdown = False
    original_handler = signal.getsignal(signal.SIGINT)

    def handle_signal(sig, frame):
        nonlocal shutdown
        if shutdown:
            signal.signal(signal.SIGINT, original_handler)
            raise KeyboardInterrupt
        shutdown = True
        log.info("Stoppen na huidige taken... (Ctrl+C nogmaals voor direct stoppen)")

    signal.signal(signal.SIGINT, handle_signal)

    succeeded = 0
    skipped = 0
    failed = 0
    start = time.time()
    batch_size = args.workers * 2

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        task_idx = 0

        while task_idx < len(tts_tasks) and not shutdown:
            batch = tts_tasks[task_idx:task_idx + batch_size]
            future_to_task = {
                pool.submit(tts_request, config, t["entry_id"], t["text"]): t
                for t in batch
            }
            task_idx += len(batch)

            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    result = future.result()
                except Exception as e:
                    log.error(f"TTS fout {config.id_prefix}{task['entry_id']}: {e}")
                    failed += 1
                    continue

                if result:
                    if result.get("created"):
                        succeeded += 1
                    else:
                        skipped += 1  # audio bestond al
                else:
                    failed += 1

            total_done = succeeded + skipped + failed
            if total_done % 50 < batch_size or task_idx >= len(tts_tasks):
                elapsed = time.time() - start
                rate = total_done / elapsed if elapsed > 0 else 0
                remaining = len(tts_tasks) - total_done
                eta = remaining / rate if rate > 0 else 0
                log.info(
                    f"[TTS {config.name}] [{total_done}/{len(tts_tasks)}] "
                    f"{total_done * 100 // len(tts_tasks)}% | "
                    f"Nieuw: {succeeded} | Bestond al: {skipped} | "
                    f"Mislukt: {failed} | {rate:.1f}/s | ETA: {eta / 60:.0f}m"
                )

    elapsed = time.time() - start
    log.info(f"═══ TTS {'Onderbroken' if shutdown else 'Klaar'} ═══")
    log.info(f"Nieuw: {succeeded} | Bestond al: {skipped} | Mislukt: {failed} | Tijd: {elapsed / 60:.1f}m")

    signal.signal(signal.SIGINT, original_handler)
    return not shutdown


# ─── TTS-OGG modus: download OGG naar addon Audio map ────────────────────
AUDIO_DIR = OUTPUT_TARGET / "Audio"


def tts_ogg_request(entry_id: int, nl_text: str) -> bool:
    """Download OGG van server, sla op in addon Audio map."""
    key = f"q{entry_id}"
    ogg_path = AUDIO_DIR / f"{key}.ogg"
    if ogg_path.exists():
        return True  # al aanwezig

    try:
        r = requests.post(
            f"{SERVER_URL}/tts/ogg",
            json={"id": key, "text": nl_text},
            timeout=120,
        )
        if r.status_code == 200 and len(r.content) > 0:
            ogg_path.write_bytes(r.content)
            return True
        log.warning(f"Server {r.status_code} voor TTS-OGG q{entry_id}")
    except requests.RequestException as e:
        log.error(f"Verbindingsfout TTS-OGG q{entry_id}: {e}")
    return False


def process_tts_ogg(config: DataTypeConfig, args) -> bool:
    """Genereer OGG audio en sla op in addon Audio map voor PlaySoundFile()."""
    log.info(f"═══ {config.name.upper()} (TTS-OGG) ═══")

    if not config.supports_tts:
        log.info(f"TTS wordt niet ondersteund voor {config.name}, overgeslagen")
        return True

    done = load_progress(config)
    if not done:
        log.warning(f"Geen vertalingen gevonden voor {config.name}")
        return True

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    # Groepeer vertalingen per entry ID
    grouped: dict[int, dict[str, str]] = {}
    for compound_key, nl_text in done.items():
        parts = compound_key.split("_", 1)
        if len(parts) != 2:
            continue
        try:
            entry_id = int(parts[0])
        except ValueError:
            continue
        if entry_id not in grouped:
            grouped[entry_id] = {}
        grouped[entry_id][parts[1]] = nl_text

    # Bouw taken: combineer description als primaire tekst
    tts_tasks = []
    already_exist = 0
    for entry_id, fields in grouped.items():
        ogg_path = AUDIO_DIR / f"q{entry_id}.ogg"
        if ogg_path.exists():
            already_exist += 1
            continue

        text = fields.get("description", "")
        if not text.strip():
            for fallback in ["completion", "objective", "title"]:
                text = fields.get(fallback, "")
                if text.strip():
                    break
        if not text.strip():
            continue
        tts_tasks.append({"entry_id": entry_id, "text": text})

    log.info(
        f"OGG taken: {len(tts_tasks)} te genereren | "
        f"Al aanwezig: {already_exist} | Audio map: {AUDIO_DIR}"
    )

    if args.dry_run or not tts_tasks:
        return True

    # Graceful shutdown
    shutdown = False
    original_handler = signal.getsignal(signal.SIGINT)

    def handle_signal(sig, frame):
        nonlocal shutdown
        if shutdown:
            signal.signal(signal.SIGINT, original_handler)
            raise KeyboardInterrupt
        shutdown = True
        log.info("Stoppen na huidige taken... (Ctrl+C nogmaals voor direct stoppen)")

    signal.signal(signal.SIGINT, handle_signal)

    succeeded = 0
    failed = 0
    start = time.time()
    batch_size = args.workers * 2

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        task_idx = 0

        while task_idx < len(tts_tasks) and not shutdown:
            batch = tts_tasks[task_idx:task_idx + batch_size]
            future_to_task = {
                pool.submit(tts_ogg_request, t["entry_id"], t["text"]): t
                for t in batch
            }
            task_idx += len(batch)

            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    ok = future.result()
                except Exception as e:
                    log.error(f"TTS-OGG fout q{task['entry_id']}: {e}")
                    failed += 1
                    continue

                if ok:
                    succeeded += 1
                else:
                    failed += 1

            total_done = succeeded + failed
            if total_done % 50 < batch_size or task_idx >= len(tts_tasks):
                elapsed = time.time() - start
                rate = total_done / elapsed if elapsed > 0 else 0
                remaining = len(tts_tasks) - total_done
                eta = remaining / rate if rate > 0 else 0
                log.info(
                    f"[TTS-OGG] [{total_done}/{len(tts_tasks)}] "
                    f"{total_done * 100 // len(tts_tasks)}% | "
                    f"OK: {succeeded} | Mislukt: {failed} | "
                    f"{rate:.1f}/s | ETA: {eta / 60:.0f}m"
                )

    elapsed = time.time() - start
    log.info(f"═══ TTS-OGG {'Onderbroken' if shutdown else 'Klaar'} ═══")
    log.info(f"Gegenereerd: {succeeded} | Mislukt: {failed} | Tijd: {elapsed / 60:.1f}m")
    log.info(f"Audio map: {AUDIO_DIR}")

    signal.signal(signal.SIGINT, original_handler)
    return not shutdown


# ─── Vertaalloop voor één data type ─────────────────────────────────────────
def process_type(config: DataTypeConfig, args,
                 retranslate_ids: set[int] | None = None,
                 retranslate_all: bool = False,
                 run_version: int = 0) -> bool:
    """Verwerk één data type. Returns True als alles succesvol was."""
    if args.tts_ogg:
        return process_tts_ogg(config, args)
    if args.tts_only:
        return process_tts_only(config, args)

    log.info(f"═══ {config.name.upper()} ═══")

    # Voortgang laden
    done = load_progress(config)
    log.info(f"Voortgang geladen: {len(done)} vertalingen")

    # Brondata parsen
    log.info(f"Parsen: {config.source_dir}")
    entries, file_ids = parse_data(config)

    if not entries:
        log.warning(f"Geen brondata gevonden voor {config.name}")
        return False

    if args.generate_only:
        generate_lua(config, done, file_ids)
        return True

    # Retranslate tracker laden voor hervatting
    already_retranslated: set[str] | None = None
    tracker: dict | None = None
    if retranslate_all or retranslate_ids:
        tracker = load_retranslate_tracker()
        if tracker.get("version") == run_version:
            already_retranslated = set(tracker.get("done_keys", []))
            log.info(f"Hervatting: {len(already_retranslated)} keys al klaar voor v{run_version}")
        else:
            # Nieuwe versie: begin opnieuw
            tracker = {"version": run_version, "done_keys": []}
            already_retranslated = set()

    # Taken opbouwen
    tasks = build_tasks(config, entries, done, retranslate_ids,
                        retranslate_all, already_retranslated)
    total_possible = sum(
        1 for e in entries.values()
        for f in config.fields
        if e.get(f, "").strip()
    )
    tts_enabled = args.tts and config.supports_tts
    log.info(
        f"Tekstvelden: {total_possible} | Al klaar: {len(done)} | "
        f"Te vertalen: {len(tasks)} | TTS: {'AAN' if tts_enabled else 'UIT'}"
    )

    if args.dry_run or not tasks:
        if not tasks:
            log.info(f"Alles is al vertaald voor {config.name}!")
        generate_lua(config, done, file_ids)
        return True

    # Graceful shutdown bij Ctrl+C
    shutdown = False
    original_handler = signal.getsignal(signal.SIGINT)

    def handle_signal(sig, frame):
        nonlocal shutdown
        if shutdown:
            signal.signal(signal.SIGINT, original_handler)
            raise KeyboardInterrupt
        shutdown = True
        log.info("Stoppen na huidige taken... (Ctrl+C nogmaals voor direct stoppen)")

    signal.signal(signal.SIGINT, handle_signal)

    # Vertalen
    succeeded = 0
    failed = 0
    start = time.time()
    save_interval = 50
    lua_interval = 500  # Lua bestanden elke 500 vertalingen herschrijven
    last_lua_write = 0
    batch_size = args.workers * 2

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        task_idx = 0

        while task_idx < len(tasks) and not shutdown:
            batch = tasks[task_idx:task_idx + batch_size]
            future_to_task = {
                pool.submit(
                    translate_text, config, t["entry_id"], t["field"],
                    t["text"], tts_enabled,
                ): t
                for t in batch
            }
            task_idx += len(batch)

            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    result = future.result()
                except Exception as e:
                    log.error(f"Fout bij {config.id_prefix}{task['entry_id']}/{task['field']}: {e}")
                    failed += 1
                    continue

                if result and result.get("nl"):
                    done[task["compound_key"]] = result["nl"]
                    succeeded += 1
                    # Tracker bijwerken voor hervertaling
                    if tracker is not None:
                        tracker["done_keys"].append(task["compound_key"])
                else:
                    failed += 1

            total_done = succeeded + failed
            if total_done % save_interval < batch_size or task_idx >= len(tasks):
                save_progress(config, done)
                if tracker is not None:
                    save_retranslate_tracker(tracker)
                elapsed = time.time() - start
                rate = total_done / elapsed if elapsed > 0 else 0
                remaining = len(tasks) - total_done
                eta = remaining / rate if rate > 0 else 0
                log.info(
                    f"[{config.name}] [{total_done}/{len(tasks)}] "
                    f"{total_done * 100 // len(tasks)}% | "
                    f"OK: {succeeded} | Mislukt: {failed} | "
                    f"{rate:.1f}/s | ETA: {eta / 60:.0f}m"
                )

            # Lua bestanden periodiek herschrijven
            if succeeded - last_lua_write >= lua_interval:
                generate_lua(config, done, file_ids)
                last_lua_write = succeeded

    # Eindresultaten opslaan
    save_progress(config, done)
    elapsed = time.time() - start
    log.info(f"═══ {config.name.upper()} {'Onderbroken' if shutdown else 'Klaar'} ═══")
    log.info(f"Vertaald: {succeeded} | Mislukt: {failed} | Tijd: {elapsed / 60:.1f}m")
    log.info(f"Totaal in cache: {len(done)}")

    # Lua bestanden genereren
    generate_lua(config, done, file_ids)

    # Herstel signal handler voor volgend type
    signal.signal(signal.SIGINT, original_handler)
    return not shutdown


# ─── Main ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="MultiLanguage_NL Pre-Cache")
    parser.add_argument(
        "--type", choices=["quests", "items", "npcs", "spells", "all"],
        default="quests",
        help="Type data om te vertalen (default: quests)",
    )
    parser.add_argument("--tts", action="store_true",
                        help="Genereer ook TTS audio (alleen voor quests)")
    parser.add_argument("--tts-only", action="store_true",
                        help="Genereer alleen TTS audio voor reeds vertaalde quests")
    parser.add_argument("--tts-ogg", action="store_true",
                        help="Download OGG audio naar addon Audio map voor PlaySoundFile()")
    parser.add_argument("--quest", type=int,
                        help="Genereer OGG voor één specifieke quest ID (impliceert --tts-ogg)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallelle verzoeken (default: 4)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Tel taken zonder te vertalen")
    parser.add_argument("--generate-only", action="store_true",
                        help="Genereer alleen Lua uit bestaande voortgang")
    parser.add_argument("--retranslate", type=str, default=None,
                        help="Hervertaal specifieke quest IDs (komma-gescheiden, bijv. 123,456,789)")
    parser.add_argument("--retranslate-all", action="store_true",
                        help="Hervertaal ALLE quests (volledige rerun, nieuwe versie)")
    parser.add_argument("--regenerate-tts", type=str, default=None,
                        help="Regenereer TTS voor specifieke quest IDs (komma-gescheiden)")
    parser.add_argument("--regenerate-tts-all", action="store_true",
                        help="Regenereer TTS voor ALLE quests (nieuwe versie)")
    parser.add_argument("--version-info", action="store_true",
                        help="Toon versie informatie en stop")
    parser.add_argument("--version-note", type=str, default=None,
                        help="Beschrijving voor deze run (opgeslagen in versie manifest)")
    parser.add_argument("--output", choices=["local", "nas"], default="local",
                        help="Schrijf output naar lokale addon (local) of NAS share (nas). Default: local")
    args = parser.parse_args()

    # Output doel instellen
    global OUTPUT_TARGET, AUDIO_DIR
    if args.output == "nas":
        if not NAS_SHARE:
            log.error("NAS share niet geconfigureerd. Zet MLNL_NAS_SHARE environment variable.")
            sys.exit(1)
        if not NAS_SHARE.exists():
            log.error(f"NAS share niet bereikbaar: {NAS_SHARE}")
            sys.exit(1)
        OUTPUT_TARGET = NAS_NL_DIR
        log.info(f"Output: NAS ({NAS_NL_DIR})")
    else:
        OUTPUT_TARGET = NL_DIR
    AUDIO_DIR = OUTPUT_TARGET / "Audio"

    # ─── Versioning ────────────────────────────────────────────────────────
    versions = load_versions()

    # Versie info tonen en stoppen
    if args.version_info:
        print_version_info(versions)
        return

    # Bepaal welke types te verwerken
    if args.type == "all":
        types_to_process = ["quests", "items", "npcs", "spells"]
    else:
        types_to_process = [args.type]

    # Bepaal retranslate IDs
    retranslate_ids: set[int] | None = None
    is_retranslate = False

    if args.retranslate_all:
        # Alle quests hervertalen: retranslate_ids = None maar wel done wissen
        is_retranslate = True
        log.info("HERVERTALING: Alle entries worden opnieuw vertaald")
    elif args.retranslate:
        try:
            retranslate_ids = {int(x.strip()) for x in args.retranslate.split(",")}
        except ValueError:
            log.error("Ongeldige quest IDs voor --retranslate. Gebruik: --retranslate 123,456,789")
            sys.exit(1)
        is_retranslate = True
        log.info(f"HERVERTALING: {len(retranslate_ids)} specifieke quests")

    # Bepaal regenerate TTS IDs
    regenerate_tts_ids: set[int] | None = None
    is_regenerate_tts = False

    if args.regenerate_tts_all:
        is_regenerate_tts = True
        log.info("TTS REGENERATIE: Alle audio wordt opnieuw gegenereerd")
    elif args.regenerate_tts:
        try:
            regenerate_tts_ids = {int(x.strip()) for x in args.regenerate_tts.split(",")}
        except ValueError:
            log.error("Ongeldige quest IDs voor --regenerate-tts. Gebruik: --regenerate-tts 123,456,789")
            sys.exit(1)
        is_regenerate_tts = True
        log.info(f"TTS REGENERATIE: {len(regenerate_tts_ids)} specifieke quests")

    log.info("═══ MultiLanguage_NL Pre-Cache ═══")
    log.info(f"Server:  {SERVER_URL}")
    log.info(f"Versie:  v{versions['current_version']}")
    log.info(f"Types:   {', '.join(types_to_process)}")
    log.info(f"Workers: {args.workers} | TTS: {'AAN' if args.tts else 'UIT'}")

    if not args.generate_only:
        # Server check (nodig voor vertaling en TTS)
        try:
            r = requests.get(f"{SERVER_URL}/health", timeout=5)
            log.info(f"Server: {r.json().get('status', '?')}")
        except Exception:
            log.error(f"Server niet bereikbaar: {SERVER_URL}")
            sys.exit(1)

    # Enkele quest OGG generatie
    if args.quest:
        config = DATA_TYPES["quests"]
        done = load_progress(config)
        qid = args.quest

        # Zoek NL tekst in voortgang
        text = None
        for field in ["description", "completion", "objective", "title"]:
            text = done.get(f"{qid}_{field}", "")
            if text.strip():
                break

        if not text or not text.strip():
            log.error(f"Geen NL vertaling gevonden voor quest {qid}. Vertaal eerst met: python precache.py")
            sys.exit(1)

        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        log.info(f"OGG genereren voor quest {qid}...")
        if tts_ogg_request(qid, text):
            ogg_path = AUDIO_DIR / f"q{qid}.ogg"
            log.info(f"OK: {ogg_path} ({ogg_path.stat().st_size / 1024:.1f} KB)")
        else:
            log.error(f"OGG generatie mislukt voor quest {qid}")
            sys.exit(1)

        log.info("═══ Alles klaar ═══")
        return

    # ─── Versie bumpen of hervatten ─────────────────────────────────────
    run_version = versions["current_version"]
    is_resume = False
    all_completed = True

    if is_retranslate and not args.dry_run:
        note = args.version_note or (
            "Volledige hervertaling" if args.retranslate_all
            else f"Hervertaling van {len(retranslate_ids)} quests"
        )
        scope = "all" if args.retranslate_all else ",".join(str(x) for x in sorted(retranslate_ids))
        run_version, is_resume = resume_or_bump(versions, "retranslate", scope, note)
        if is_resume:
            log.info(f"Hervatting van v{run_version}")
        else:
            log.info(f"Nieuwe versie: v{run_version}")
        save_versions(versions)

    if is_regenerate_tts and not args.dry_run:
        note = args.version_note or (
            "Volledige TTS regeneratie" if args.regenerate_tts_all
            else f"TTS regeneratie van {len(regenerate_tts_ids)} quests"
        )
        scope = "all" if args.regenerate_tts_all else ",".join(str(x) for x in sorted(regenerate_tts_ids))
        run_version, is_resume = resume_or_bump(versions, "regenerate_tts", scope, note)
        if not is_resume:
            log.info(f"Nieuwe versie: v{run_version}")
        save_versions(versions)

    # TTS regeneratie: verwijder bestaande OGG bestanden zodat ze opnieuw worden gegenereerd
    if is_regenerate_tts and not is_resume:
        removed_ogg = 0
        if args.regenerate_tts_all:
            for ogg_file in AUDIO_DIR.glob("q*.ogg"):
                ogg_file.unlink()
                removed_ogg += 1
        elif regenerate_tts_ids:
            for qid in regenerate_tts_ids:
                ogg_path = AUDIO_DIR / f"q{qid}.ogg"
                if ogg_path.exists():
                    ogg_path.unlink()
                    removed_ogg += 1
        if removed_ogg:
            log.info(f"TTS regeneratie: {removed_ogg} OGG bestanden verwijderd")
        args.tts_ogg = True

    # Verwerk elk type
    for type_name in types_to_process:
        config = DATA_TYPES[type_name]
        completed = process_type(
            config, args, retranslate_ids,
            retranslate_all=args.retranslate_all,
            run_version=run_version,
        )
        if not completed:
            all_completed = False
            break

    # ─── Versie tracking bijwerken ───────────────────────────────────────
    if not args.dry_run and (is_retranslate or is_regenerate_tts):
        if all_completed:
            mark_run_completed(versions)
            clear_retranslate_tracker()
            log.info(f"Run v{run_version} voltooid!")

            if is_retranslate:
                if args.retranslate_all:
                    versions["quest_versions"] = {}
                elif retranslate_ids:
                    stamp_quest_versions(versions, list(retranslate_ids), run_version, "translation")

            if is_regenerate_tts:
                if args.regenerate_tts_all:
                    versions["tts_versions"] = {}
                elif regenerate_tts_ids:
                    stamp_quest_versions(versions, list(regenerate_tts_ids), run_version, "tts")
        else:
            log.info(f"Run v{run_version} onderbroken — hervat met hetzelfde commando")

        # Stats bijwerken
        if versions["runs"]:
            run_entry = versions["runs"][-1]
            config = DATA_TYPES["quests"]
            done = load_progress(config)
            run_entry["stats"]["quests_in_cache"] = len(set(
                k.split("_")[0] for k in done.keys()
            ))
            if is_regenerate_tts:
                ogg_count = len(list(AUDIO_DIR.glob("q*.ogg"))) if AUDIO_DIR.exists() else 0
                run_entry["stats"]["ogg_count"] = ogg_count

    save_versions(versions)
    log.info(f"═══ Alles klaar (v{versions['current_version']}) ═══")


if __name__ == "__main__":
    main()
