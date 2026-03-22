"""
MultiLanguage_NL FastAPI Server
Draait in Docker op TrueNAS bare metal (via TrueNAS Apps).
- Vertaalt quest teksten lokaal via Opus MT (Helsinki-NLP) + CTranslate2 (INT8)
- Genereert TTS audio via Piper (draait in HA VM)
- Vertalingen worden NIET gecacht in translations.json (precache.py slaat op als Lua)
"""

import os
import json
import re
import base64
import logging
import asyncio
import fcntl
from pathlib import Path
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from threading import Lock
from time import monotonic

import ctranslate2
import sentencepiece as spm
from transformers import AutoTokenizer
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("MultiLanguage_NL")

# ─── Configuratie ─────────────────────────────────────────────────────────────
PIPER_URL   = os.getenv("PIPER_URL",   "http://piper:10200")
PIPER_VOICE = os.getenv("PIPER_VOICE", "nl_BE-nathalie-medium")
MT_MODEL    = os.getenv("MT_MODEL",    "Helsinki-NLP/opus-mt-en-nl")
CT2_DIR     = Path(os.getenv("CT2_DIR", "/models/opus-mt-en-nl-ct2"))
DATA_DIR    = Path(os.getenv("DATA_DIR", "/data"))
MT_BEAM_SIZE = int(os.getenv("MT_BEAM_SIZE", "4"))

GLOSSARY_FILE = DATA_DIR / "glossary.json"
AUDIO_DIR     = DATA_DIR / "audio"

# ─── JSON helpers (alleen nog voor glossary) ─────────────────────────────────
_thread_lock = Lock()

@contextmanager
def _locked_file(path: Path, mode: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, mode, encoding="utf-8")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield fh
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()

def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with _locked_file(path, "r") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        log.warning(f"Kon {path} niet lezen, terugvallen op default.")
        return default

def _write_json(path: Path, data):
    tmp = path.with_suffix(".tmp")
    with _locked_file(tmp, "w") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)

# ─── Glossary ─────────────────────────────────────────────────────────────────
DEFAULT_GLOSSARY = [
    {"source_term": "Sargeras",        "keep_as": "Sargeras",        "category": "personage", "notes": ""},
    {"source_term": "Elune",           "keep_as": "Elune",           "category": "godheid",   "notes": ""},
    {"source_term": "Ner'zhul",        "keep_as": "Ner'zhul",        "category": "personage", "notes": ""},
    {"source_term": "Arthas",          "keep_as": "Arthas",          "category": "personage", "notes": ""},
    {"source_term": "Sylvanas",        "keep_as": "Sylvanas",        "category": "personage", "notes": ""},
    {"source_term": "Thrall",          "keep_as": "Thrall",          "category": "personage", "notes": ""},
    {"source_term": "Jaina",           "keep_as": "Jaina",           "category": "personage", "notes": ""},
    {"source_term": "Anduin",          "keep_as": "Anduin",          "category": "personage", "notes": ""},
    {"source_term": "Tyrande",         "keep_as": "Tyrande",         "category": "personage", "notes": ""},
    {"source_term": "Malfurion",       "keep_as": "Malfurion",       "category": "personage", "notes": ""},
    {"source_term": "Illidan",         "keep_as": "Illidan",         "category": "personage", "notes": ""},
    {"source_term": "Alliance",        "keep_as": "Alliance",        "category": "fractie",   "notes": ""},
    {"source_term": "Horde",           "keep_as": "Horde",           "category": "fractie",   "notes": ""},
    {"source_term": "Cenarion Circle", "keep_as": "Cenarion Circle", "category": "fractie",   "notes": ""},
    {"source_term": "Argent Dawn",     "keep_as": "Argent Dawn",     "category": "fractie",   "notes": ""},
    {"source_term": "Scarlet Crusade", "keep_as": "Scarlet Crusade", "category": "fractie",   "notes": ""},
    {"source_term": "Azeroth",         "keep_as": "Azeroth",         "category": "wereld",    "notes": ""},
    {"source_term": "Stormwind",       "keep_as": "Stormwind",       "category": "locatie",   "notes": ""},
    {"source_term": "Orgrimmar",       "keep_as": "Orgrimmar",       "category": "locatie",   "notes": ""},
    {"source_term": "Ironforge",       "keep_as": "Ironforge",       "category": "locatie",   "notes": ""},
    {"source_term": "Thunder Bluff",   "keep_as": "Thunder Bluff",   "category": "locatie",   "notes": ""},
    {"source_term": "Dalaran",         "keep_as": "Dalaran",         "category": "locatie",   "notes": ""},
    {"source_term": "Silvermoon",      "keep_as": "Silvermoon",      "category": "locatie",   "notes": ""},
    {"source_term": "Undercity",       "keep_as": "Undercity",       "category": "locatie",   "notes": ""},
    {"source_term": "Boralus",         "keep_as": "Boralus",         "category": "locatie",   "notes": ""},
    {"source_term": "Valdrakken",      "keep_as": "Valdrakken",      "category": "locatie",   "notes": ""},
    {"source_term": "Elwynn Forest",   "keep_as": "Elwynn Forest",   "category": "zone",      "notes": ""},
    {"source_term": "Durotar",         "keep_as": "Durotar",         "category": "zone",      "notes": ""},
    {"source_term": "Northrend",       "keep_as": "Northrend",       "category": "zone",      "notes": ""},
    {"source_term": "Outland",         "keep_as": "Outland",         "category": "zone",      "notes": ""},
    {"source_term": "Shadowlands",     "keep_as": "Shadowlands",     "category": "zone",      "notes": ""},
    {"source_term": "Dragon Isles",    "keep_as": "Dragon Isles",    "category": "zone",      "notes": ""},
    {"source_term": "The Emerald Dream","keep_as":"The Emerald Dream","category": "zone",      "notes": ""},
    {"source_term": "Night Elf",       "keep_as": "Night Elf",       "category": "ras",       "notes": ""},
    {"source_term": "Draenei",         "keep_as": "Draenei",         "category": "ras",       "notes": ""},
    {"source_term": "Pandaren",        "keep_as": "Pandaren",        "category": "ras",       "notes": ""},
    {"source_term": "Forsaken",        "keep_as": "Forsaken",        "category": "ras",       "notes": ""},
    {"source_term": "Blood Elf",       "keep_as": "Blood Elf",       "category": "ras",       "notes": ""},
    {"source_term": "Death Knight",    "keep_as": "Death Knight",    "category": "klasse",    "notes": ""},
    {"source_term": "Demon Hunter",    "keep_as": "Demon Hunter",    "category": "klasse",    "notes": ""},
    {"source_term": "Evoker",          "keep_as": "Evoker",          "category": "klasse",    "notes": ""},
    {"source_term": "Hearthstone",     "keep_as": "Hearthstone",     "category": "item",      "notes": ""},
    {"source_term": "Gold",            "keep_as": "Goud",            "category": "valuta",    "notes": ""},
    {"source_term": "Silver",          "keep_as": "Zilver",          "category": "valuta",    "notes": ""},
    {"source_term": "Copper",          "keep_as": "Koper",           "category": "valuta",    "notes": ""},
]

def glossary_load() -> list[dict]:
    data = _read_json(GLOSSARY_FILE, None)
    if data is None:
        _write_json(GLOSSARY_FILE, DEFAULT_GLOSSARY)
        return DEFAULT_GLOSSARY
    return data

def glossary_save(items: list[dict]):
    with _thread_lock:
        _write_json(GLOSSARY_FILE, items)

# ─── CTranslate2 Opus MT model ──────────────────────────────────────────────
translator: ctranslate2.Translator | None = None
tokenizer: AutoTokenizer | None = None

def load_mt_model():
    global translator, tokenizer

    if not (CT2_DIR / "model.bin").exists():
        raise RuntimeError(
            f"CTranslate2 model niet gevonden in {CT2_DIR}. "
            "Het model wordt geconverteerd tijdens de Docker build."
        )

    # Threads per worker: totale cores / aantal uvicorn workers
    # Bij 3 workers op 16 threads → 5 threads per worker
    uvicorn_workers = int(os.getenv("WEB_CONCURRENCY", "3"))
    cpu_count = os.cpu_count() or 16
    threads_per_worker = max(2, cpu_count // uvicorn_workers)

    log.info(f"CTranslate2 model laden: {CT2_DIR}")
    translator = ctranslate2.Translator(
        str(CT2_DIR),
        device="cpu",
        inter_threads=1,
        intra_threads=threads_per_worker,
    )
    tokenizer = AutoTokenizer.from_pretrained(MT_MODEL)
    log.info(f"CTranslate2 INT8 model geladen. Workers: {uvicorn_workers}, threads/worker: {threads_per_worker}")

# ─── Vertaallogica ────────────────────────────────────────────────────────────
PLACEHOLDER_RE = re.compile(
    r'(\|c[0-9a-fA-F]{8}|\|r|\|H[^|]+\|h|\|h|\{[^}]+\}|PROT\w+?PROT)'
    # NB: [q] en [q0]-[q8] tags worden NIET beschermd — die moeten in de output blijven.
    # Eerder matchte \[[^\]]+\] alle [...] content inclusief [q] tags.
)

def protect_terms(text: str, glossary: list[dict]) -> tuple[str, dict]:
    """Bescherm glossary termen en WoW codes tegen vertaling.

    Vervangt beschermde termen met korte placeholder woorden die het model
    als onbekende eigennamen doorgeeft zonder te vertalen.
    Gebruikt formaat: PHxNN (bijv. PHa00, PHb01) — ziet eruit als een acroniem.
    """
    restore = {}
    counter = [0]

    def make_token(original: str) -> str:
        idx = counter[0]
        counter[0] += 1
        # PHxNN: korte "eigennaam"-achtige token, afwisselend prefix voor variatie
        prefix = chr(ord('a') + (idx % 26))
        token = f"PH{prefix}{idx:02d}"
        restore[token] = original
        return token

    def replace_wow(m):
        return make_token(m.group(0))
    text = PLACEHOLDER_RE.sub(replace_wow, text)

    for entry in sorted(glossary, key=lambda g: -len(g["source"])):
        src  = entry["source"]
        keep = entry["keep"]
        # Gebruik word boundaries zodat "Heal" niet matcht in "health"
        pattern = re.compile(r"\b" + re.escape(src) + r"\b", re.IGNORECASE)
        if pattern.search(text):
            token = make_token(keep)
            text = pattern.sub(token, text)

    return text, restore

def restore_terms(text: str, restore: dict) -> str:
    # Sorteer tokens op lengte (langste eerst) om partial matches te voorkomen
    for token in sorted(restore.keys(), key=len, reverse=True):
        # Case-insensitive restore: model kan hoofdletters wijzigen
        # Ook matchen als model het token aan een woord plakt (geen spatie)
        pattern = re.compile(re.escape(token), re.IGNORECASE)
        text = pattern.sub(restore[token], text)

    # Vang gelekte tokens op: model kan prefix/suffix toevoegen (bijv. "FA00Forms")
    text = re.sub(r"PH[a-z]\d{2}", "", text, flags=re.IGNORECASE)
    # Opruimen na token-verwijdering
    text = re.sub(r"  +", " ", text)
    text = text.strip()
    return text

def translate_locally(text: str, glossary: list[dict]) -> str:
    """Vertaal één tekst (met zin-splitsing)."""
    if translator is None or tokenizer is None:
        raise RuntimeError("CTranslate2 model niet geladen.")

    protected, restore = protect_terms(text, glossary)

    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+(?=[A-Z])', protected) if s.strip()]
    if not sentences:
        return text

    source_tokens = []
    for sent in sentences:
        tokens = tokenizer.convert_ids_to_tokens(tokenizer.encode(sent))
        source_tokens.append(tokens)

    results = translator.translate_batch(
        source_tokens,
        beam_size=MT_BEAM_SIZE,
        length_penalty=0.6,
    )

    translated = []
    for result in results:
        token_ids = tokenizer.convert_tokens_to_ids(result.hypotheses[0])
        translated.append(tokenizer.decode(token_ids, skip_special_tokens=True))

    return restore_terms(" ".join(translated), restore)


def translate_batch_locally(texts: list[dict], glossary: list[dict]) -> dict[str, str]:
    """Vertaal meerdere teksten tegelijk met één grote CTranslate2 batch.

    Combineert alle zinnen uit alle teksten in één translate_batch() call
    zodat CTranslate2 maximaal kan parallelliseren over alle CPU cores.
    Elke zin wordt volledig onafhankelijk vertaald — geen kruisbestuiving.

    texts: [{"id": "...", "text": "..."}, ...]
    Returns: {id: nl_text}
    """
    if translator is None or tokenizer is None:
        raise RuntimeError("CTranslate2 model niet geladen.")

    # Stap 1: bereid alle teksten voor en verzamel alle zinnen
    prepared_items = []  # (id, restore, sentence_indices)
    all_tokens = []      # platte lijst van token-lijsten voor CT2

    for item in texts:
        protected, restore = protect_terms(item["text"], glossary)
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+(?=[A-Z])', protected) if s.strip()]

        if not sentences:
            prepared_items.append((item["id"], restore, []))
            continue

        start_idx = len(all_tokens)
        for sent in sentences:
            tokens = tokenizer.convert_ids_to_tokens(tokenizer.encode(sent))
            all_tokens.append(tokens)
        end_idx = len(all_tokens)

        prepared_items.append((item["id"], restore, list(range(start_idx, end_idx))))

    if not all_tokens:
        return {item["id"]: item["text"] for item in texts}

    # Stap 2: één grote batch vertaling — maximale CPU benutting
    all_results = translator.translate_batch(
        all_tokens,
        beam_size=MT_BEAM_SIZE,
        length_penalty=0.6,
    )

    # Stap 3: decodeer en groepeer per tekst
    all_decoded = []
    for result in all_results:
        token_ids = tokenizer.convert_tokens_to_ids(result.hypotheses[0])
        all_decoded.append(tokenizer.decode(token_ids, skip_special_tokens=True))

    output = {}
    for item_id, restore, indices in prepared_items:
        if not indices:
            orig = next((t["text"] for t in texts if t["id"] == item_id), "")
            output[item_id] = orig
        else:
            translated_sents = [all_decoded[i] for i in indices]
            output[item_id] = restore_terms(" ".join(translated_sents), restore)

    return output

# ─── TTS via Wyoming Piper ────────────────────────────────────────────────────
async def synthesize_tts(text: str) -> bytes | None:
    """
    Wyoming protocol v1 — correct frame formaat:
      <JSON header>\n
      <data_length bytes>       ← extra data blob (bijv. audio info), geen newline
      <payload_length bytes>    ← ruwe PCM payload
    """
    host = PIPER_URL.replace("http://", "").split(":")[0]
    try:
        port = int(PIPER_URL.rsplit(":", 1)[-1])
    except ValueError:
        port = 10200

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5
        )

        req = json.dumps({
            "type":           "synthesize",
            "data_length":    0,
            "payload_length": 0,
            "data": {
                "text":  text,
                "voice": {"name": PIPER_VOICE, "language": "nl_NL"},
            },
        }) + "\n"
        writer.write(req.encode())
        await writer.drain()

        audio_chunks = []

        while True:
            header_line = await asyncio.wait_for(reader.readline(), timeout=15)
            if not header_line:
                break

            header_line = header_line.strip()
            header = json.loads(header_line.decode())

            event_type     = header.get("type", "")
            data_length    = header.get("data_length", 0)
            payload_length = header.get("payload_length", 0)

            if data_length > 0:
                await asyncio.wait_for(reader.readexactly(data_length), timeout=10)

            if payload_length > 0:
                payload = await asyncio.wait_for(
                    reader.readexactly(payload_length), timeout=10
                )
                audio_chunks.append(payload)

            if event_type == "audio-start":
                log.info("TTS audio-start.")
            elif event_type == "audio-chunk":
                pass
            elif event_type == "audio-stop":
                log.info(f"TTS klaar — {len(audio_chunks)} chunks, "
                         f"{sum(len(c) for c in audio_chunks)} bytes PCM.")
                break
            elif event_type == "error":
                log.error(f"Piper fout: {header}")
                break

        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=2)
        except Exception:
            pass

        if not audio_chunks:
            log.warning("Piper stuurde geen audio chunks.")
            return None

        import wave, io
        pcm = b"".join(audio_chunks)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(pcm)
        buf.seek(0)
        return buf.read()

    except asyncio.TimeoutError:
        log.error("TTS timeout.")
    except Exception as e:
        log.error(f"TTS verbindingsfout: {e}")
    return None

# ─── App lifespan ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    load_mt_model()
    glossary_load()
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Data map: {DATA_DIR}")
    log.info(f"Glossary:    {GLOSSARY_FILE}")
    log.info(f"Audio cache: {AUDIO_DIR}")
    yield
    # Sla all-time metrics op bij shutdown
    _save_alltime()
    log.info("All-time metrics opgeslagen.")

app = FastAPI(title="MultiLanguage_NL Server", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── Runtime metrics ─────────────────────────────────────────────────────────
_metrics_lock = Lock()
METRICS_FILE = DATA_DIR / "metrics_alltime.json"

def _load_alltime() -> dict:
    """Laad all-time tellers van disk."""
    if METRICS_FILE.exists():
        try:
            return json.loads(METRICS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "translate_requests": 0,
        "translate_texts": 0,
        "translate_errors": 0,
        "tts_requests": 0,
    }

def _save_alltime():
    """Schrijf all-time tellers naar disk."""
    try:
        METRICS_FILE.write_text(json.dumps({
            "translate_requests": _metrics["alltime_translate_requests"],
            "translate_texts": _metrics["alltime_translate_texts"],
            "translate_errors": _metrics["alltime_translate_errors"],
            "tts_requests": _metrics["alltime_tts_requests"],
        }))
    except OSError as e:
        log.warning(f"Metrics opslaan mislukt: {e}")

_alltime = _load_alltime()
_metrics = {
    "started_at": monotonic(),
    "started_at_utc": datetime.now(timezone.utc).isoformat(),
    # Sessie tellers (reset bij herstart)
    "translate_requests": 0,
    "translate_texts": 0,
    "translate_errors": 0,
    "total_translate_ms": 0.0,
    "tts_requests": 0,
    # All-time tellers (persistent)
    "alltime_translate_requests": _alltime["translate_requests"],
    "alltime_translate_texts": _alltime["translate_texts"],
    "alltime_translate_errors": _alltime["translate_errors"],
    "alltime_tts_requests": _alltime["tts_requests"],
}
_save_counter = 0  # sla elke N requests op, niet elke keer

def _record_translate(text_count: int, duration_ms: float, error: bool = False):
    global _save_counter
    with _metrics_lock:
        _metrics["translate_requests"] += 1
        _metrics["translate_texts"] += text_count
        _metrics["total_translate_ms"] += duration_ms
        _metrics["alltime_translate_requests"] += 1
        _metrics["alltime_translate_texts"] += text_count
        if error:
            _metrics["translate_errors"] += 1
            _metrics["alltime_translate_errors"] += 1
        _save_counter += 1
        if _save_counter >= 50:
            _save_counter = 0
            _save_alltime()

@app.get("/health")
async def health():
    return {
        "status":              "ok",
        "timestamp":           datetime.now(timezone.utc).isoformat(),
        "model":               MT_MODEL,
        "engine":              "ctranslate2-int8",
        "beam_size":           MT_BEAM_SIZE,
        "data_dir":            str(DATA_DIR),
        "glossary_exists":     GLOSSARY_FILE.exists(),
    }

app.mount("/static", StaticFiles(directory="static"), name="static")

# ─── Pydantic modellen ────────────────────────────────────────────────────────
class TranslateRequest(BaseModel):
    id:       str
    quest_id: int | None = None
    text:     str
    tts:      bool = False

class TranslateResponse(BaseModel):
    id:     str
    nl:     str
    audio:  str | None = None
    cached: bool = False

class GlossaryItem(BaseModel):
    source_term: str
    keep_as:     str
    category:    str = "general"
    notes:       str | None = None

# ─── Hulpfunctie ──────────────────────────────────────────────────────────────
def audio_get(key: str) -> bytes | None:
    path = AUDIO_DIR / f"{key}.wav"
    return path.read_bytes() if path.exists() else None

def audio_set(key: str, data: bytes):
    path = AUDIO_DIR / f"{key}.wav"
    path.write_bytes(data)


async def get_or_synthesize(key: str, nl_text: str, tts: bool):
    if not tts:
        return None
    cached = await asyncio.get_event_loop().run_in_executor(None, audio_get, key)
    if cached:
        log.info(f"Audio cache hit: {key}")
        return base64.b64encode(cached).decode()
    raw = await synthesize_tts(nl_text)
    if raw:
        await asyncio.get_event_loop().run_in_executor(None, audio_set, key, raw)
        return base64.b64encode(raw).decode()
    return None

async def get_glossary() -> list[dict]:
    return await asyncio.get_event_loop().run_in_executor(None, glossary_load)

# ─── Endpoints ────────────────────────────────────────────────────────────────
@app.post("/translate", response_model=TranslateResponse)
async def translate(req: TranslateRequest):
    glossary = await get_glossary()
    gl = [{"source": g["source_term"], "keep": g["keep_as"]} for g in glossary]
    t0 = monotonic()
    try:
        nl_text = await asyncio.get_event_loop().run_in_executor(
            None, translate_locally, req.text, gl
        )
    except Exception as e:
        _record_translate(1, (monotonic() - t0) * 1000, error=True)
        log.error(f"Vertaalfout: {e}")
        raise HTTPException(500, f"Vertaling mislukt: {e}")
    _record_translate(1, (monotonic() - t0) * 1000)

    audio = await get_or_synthesize(req.id, nl_text, req.tts)

    return TranslateResponse(id=req.id, nl=nl_text, audio=audio, cached=False)


class BatchTranslateItem(BaseModel):
    id:   str
    text: str

class BatchTranslateRequest(BaseModel):
    items: list[BatchTranslateItem]

class BatchTranslateResponse(BaseModel):
    results: list[TranslateResponse]

@app.post("/translate/batch", response_model=BatchTranslateResponse)
async def translate_batch(req: BatchTranslateRequest):
    """Vertaal meerdere teksten in één request via één grote CTranslate2 batch."""
    glossary = await get_glossary()
    gl = [{"source": g["source_term"], "keep": g["keep_as"]} for g in glossary]

    items = [{"id": item.id, "text": item.text} for item in req.items]
    t0 = monotonic()
    try:
        translations = await asyncio.get_event_loop().run_in_executor(
            None, translate_batch_locally, items, gl
        )
    except Exception as e:
        _record_translate(len(items), (monotonic() - t0) * 1000, error=True)
        log.error(f"Batch vertaalfout: {e}")
        raise HTTPException(500, f"Batch vertaling mislukt: {e}")
    _record_translate(len(items), (monotonic() - t0) * 1000)

    results = [
        TranslateResponse(id=item.id, nl=translations.get(item.id, ""), audio=None, cached=False)
        for item in req.items
    ]
    return BatchTranslateResponse(results=results)


class TtsRequest(BaseModel):
    id:   str
    text: str

class TtsResponse(BaseModel):
    id:      str
    created: bool

@app.post("/tts", response_model=TtsResponse)
async def tts(req: TtsRequest):
    """Genereer alleen TTS audio voor een reeds vertaalde tekst."""
    with _metrics_lock:
        _metrics["tts_requests"] += 1
        _metrics["alltime_tts_requests"] += 1
    existing = await asyncio.get_event_loop().run_in_executor(None, audio_get, req.id)
    if existing:
        return TtsResponse(id=req.id, created=False)
    raw = await synthesize_tts(req.text)
    if not raw:
        raise HTTPException(500, "TTS synthese mislukt")
    await asyncio.get_event_loop().run_in_executor(None, audio_set, req.id, raw)
    return TtsResponse(id=req.id, created=True)


class TtsOggRequest(BaseModel):
    id:   str
    text: str

@app.post("/tts/ogg")
async def tts_ogg(req: TtsOggRequest):
    """Genereer TTS audio en retourneer als OGG Vorbis."""
    import subprocess, io

    # Check of OGG al gecacht is
    ogg_path = AUDIO_DIR / f"{req.id}.ogg"
    if ogg_path.exists():
        return Response(content=ogg_path.read_bytes(), media_type="audio/ogg")

    # Genereer WAV (of gebruik cache)
    wav_data = await asyncio.get_event_loop().run_in_executor(None, audio_get, req.id)
    if not wav_data:
        wav_data = await synthesize_tts(req.text)
        if not wav_data:
            raise HTTPException(500, "TTS synthese mislukt")
        await asyncio.get_event_loop().run_in_executor(None, audio_set, req.id, wav_data)

    # Converteer WAV → OGG via ffmpeg
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", "pipe:0", "-c:a", "libvorbis", "-q:a", "3",
             "-f", "ogg", "pipe:1"],
            input=wav_data, capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            log.error(f"ffmpeg fout: {result.stderr.decode()}")
            raise HTTPException(500, "OGG conversie mislukt")
    except FileNotFoundError:
        raise HTTPException(500, "ffmpeg niet gevonden in container")

    ogg_data = result.stdout
    await asyncio.get_event_loop().run_in_executor(
        None, lambda: ogg_path.write_bytes(ogg_data)
    )
    log.info(f"OGG gegenereerd: {req.id} ({len(ogg_data)} bytes)")

    return Response(content=ogg_data, media_type="audio/ogg")


@app.get("/audio/{key}.ogg")
async def get_audio_ogg(key: str):
    """Stream gecachte OGG audio."""
    ogg_path = AUDIO_DIR / f"{key}.ogg"
    if not ogg_path.exists():
        raise HTTPException(404, "OGG audio niet gevonden.")
    return Response(content=ogg_path.read_bytes(), media_type="audio/ogg")


@app.get("/stats")
async def stats():
    audio_wav = len(list(AUDIO_DIR.glob("*.wav"))) if AUDIO_DIR.exists() else 0
    audio_ogg = len(list(AUDIO_DIR.glob("*.ogg"))) if AUDIO_DIR.exists() else 0
    glossary = await get_glossary()

    with _metrics_lock:
        m = dict(_metrics)

    uptime_s = monotonic() - m["started_at"]
    avg_ms = (m["total_translate_ms"] / m["translate_requests"]) if m["translate_requests"] else 0

    return {
        "uptime_seconds": round(uptime_s),
        "started_at": m["started_at_utc"],
        # Sessie metrics
        "translate_requests": m["translate_requests"],
        "translate_texts": m["translate_texts"],
        "translate_errors": m["translate_errors"],
        "avg_translate_ms": round(avg_ms, 1),
        "tts_requests": m["tts_requests"],
        # All-time metrics
        "alltime_translate_requests": m["alltime_translate_requests"],
        "alltime_translate_texts": m["alltime_translate_texts"],
        "alltime_translate_errors": m["alltime_translate_errors"],
        "alltime_tts_requests": m["alltime_tts_requests"],
        # Overig
        "audio_wav_cached": audio_wav,
        "audio_ogg_cached": audio_ogg,
        "glossary_terms": len(glossary),
    }

@app.get("/glossary")
async def list_glossary(category: str | None = None):
    items = await get_glossary()
    if category:
        items = [i for i in items if i.get("category") == category]
    return items

@app.post("/glossary")
async def add_glossary(item: GlossaryItem):
    items = await get_glossary()
    items = [i for i in items if i["source_term"] != item.source_term]
    items.append(item.model_dump())
    await asyncio.get_event_loop().run_in_executor(None, glossary_save, items)
    return {"ok": True}

@app.delete("/glossary/{source_term}")
async def delete_glossary(source_term: str):
    items = await get_glossary()
    items = [i for i in items if i["source_term"] != source_term]
    await asyncio.get_event_loop().run_in_executor(None, glossary_save, items)
    return {"ok": True}

@app.get("/")
async def ui():
    return FileResponse("static/index.html")

@app.get("/audio/{key}")
async def get_audio(key: str):
    """Stream gecachte WAV audio naar de browser."""
    audio_path = AUDIO_DIR / f"{key}.wav"
    if not audio_path.exists():
        raise HTTPException(404, "Audio niet gevonden.")
    return Response(
        content=audio_path.read_bytes(),
        media_type="audio/wav",
    )
