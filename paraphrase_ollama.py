#!/usr/bin/env python3
"""
Paraphrase a large Excel using a LOCAL Ollama model via HTTP (no ollama Python pkg).

- Works with Ollama at http://localhost:11434
- Uses streaming /api/generate and assembles the response
- Batch processing + resumable checkpoints
- Persistent SQLite cache to skip duplicates
- Exponential backoff with jitter on transient errors
- Final Excel written after completion

Usage:
  # optional: create a venv first (recommended)
  python3 -m venv .venv && source .venv/bin/activate
  pip install pandas tqdm xlsxwriter python-dotenv requests
  OLLAMA_MODEL="llama3.2:3b-instruct-q4_K_M" python paraphrase_ollama_http.py
"""

import os
import time
import json
import math
import random
import hashlib
import sqlite3
from contextlib import closing
from typing import Dict, List, Optional

import pandas as pd
import requests
from tqdm import tqdm

# =========================
# ---- CONFIGURATIONS -----
# =========================

# Your input file name (as you told me)
INPUT_XLSX  = "buysm_products_all_fullinfo.xlsx"
OUTPUT_XLSX = "buysm_products_all_fullinfo_paraphrased.xlsx"

CHECKPOINT_PARQUET   = "paraphrase_checkpoint.parquet"
PROCESSED_ROWS_JSON  = "processed_rows.json"
SQLITE_CACHE         = "paraphrase_cache.sqlite"

# Ollama settings
OLLAMA_URL  = os.getenv("OLLAMA_URL", "http://localhost:11434")  # change if remote
MODEL_NAME  = os.getenv("OLLAMA_MODEL", "llama3.2:3b-instruct-q4_K_M")

# Batch & retry tuning for ~2.6k rows
BATCH_SIZE                = 200
CHECKPOINT_EVERY_BATCHES  = 1
API_CALL_DELAY_SEC        = 0.4
MAX_RETRIES               = 6
BASE_BACKOFF              = 1.0

# Generation options (tune for clarity & memory)
OLLAMA_OPTIONS = {
    "temperature": 0.2,
    "top_p": 0.9,
    "repeat_penalty": 1.05,
    "num_predict": -1,  # unlimited tokens
    "num_ctx": 1024,    # keep memory small
}

FIELDS_TO_PARAPHRASE: List[str] = [
    "Introduction", "Uses", "Benefits", "Side Effects",
    "Most Common Side Effects", "Common Side Effects",
    "How to Use", "How it Works", "Safety Advice"
]

PROMPTS: Dict[str, str] = {
    "Introduction": "Paraphrase the medical introduction below, rewording it to be unique and plagiarism-free, but keep it concise, accurate, and ready for patient education. Do not include introductions, explanations, or formatting—just the rewritten text:\n\n{}",
    "Uses": "Paraphrase the 'Uses' section below as unique, plagiarism-free medical guidance. Only return text usable in production—skip all explanations or introductions:\n\n{}",
    "Benefits": "Paraphrase the 'Benefits' info below in a concise, factual, and production-friendly way. No explanation—just reword and return improved content:\n\n{}",
    "Side Effects": "Paraphrase the 'Side Effects' section so that it is unique, clear, and suitable for direct publication. Return only the main body content:\n\n{}",
    "Most Common Side Effects": "Paraphrase the 'Most Common Side Effects' info below for a patient handout. No descriptions, options, or meta explanation—just the final result:\n\n{}",
    "Common Side Effects": "Rewrite the 'Common Side Effects' text below as a unique, high-quality summary suitable for direct use on a medical information page. No headers or extra text, just the paraphrased result:\n\n{}",
    "How to Use": "Paraphrase the 'How to Use' instructions for a medicine in clear, direct, and production-ready language with no extra explanation:\n\n{}",
    "How it Works": "Paraphrase the medical 'How it Works' details into unique, publication-ready prose suitable for patient education. Return only the text, no headers or disclaimers:\n\n{}",
    "Safety Advice": "Rewrite the 'Safety Advice' below in a unique and plagiarism-free way suitable for production, omitting any extra headers or prefatory comments:\n\n{}"
}

DEFAULT_PROMPT = "Paraphrase clearly for direct publication, omit explanation or formatting:\n\n{}"

# =========================
# ---- CACHE & HELPERS ----
# =========================

def sha_key(col: str, text: str) -> str:
    h = hashlib.sha256()
    h.update(("COL:" + str(col) + "\nTEXT:" + str(text)).encode("utf-8"))
    return h.hexdigest()

def init_sqlite_cache(db_path: str = SQLITE_CACHE):
    with closing(sqlite3.connect(db_path)) as conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            id TEXT PRIMARY KEY,
            col TEXT,
            input TEXT,
            output TEXT
        )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_col ON cache(col)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_id ON cache(id)")
        conn.commit()

def cache_get(col: str, text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return text
    k = sha_key(col, text)
    with closing(sqlite3.connect(SQLITE_CACHE)) as conn:
        c = conn.cursor()
        c.execute("SELECT output FROM cache WHERE id=?", (k,))
        row = c.fetchone()
        return row[0] if row and isinstance(row[0], str) else ""

def cache_put(col: str, text: str, output: str):
    k = sha_key(col, text)
    with closing(sqlite3.connect(SQLITE_CACHE)) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO cache (id, col, input, output) VALUES (?, ?, ?, ?)",
            (k, col, text, output),
        )
        conn.commit()

def load_processed_rows() -> set:
    if os.path.exists(PROCESSED_ROWS_JSON):
        try:
            with open(PROCESSED_ROWS_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data if isinstance(data, list) else [])
        except Exception:
            return set()
    return set()

def save_processed_rows(s: set):
    try:
        with open(PROCESSED_ROWS_JSON, "w", encoding="utf-8") as f:
            json.dump(sorted(list(s)), f)
    except Exception as e:
        print(f"Warning: failed to save {PROCESSED_ROWS_JSON}: {e}")

def backoff_sleep(attempt: int):
    delay = BASE_BACKOFF * (2 ** attempt) + random.uniform(0.0, 0.5)
    time.sleep(delay)

# =========================
# ---- OLLAMA HTTP CALL ---
# =========================

def ollama_generate_http(prompt: str, model: str, url: str, options: dict, timeout: int = 300) -> str:
    """
    Calls Ollama /api/generate with streaming, assembles final text.
    Returns the concatenated 'response' text.
    """
    endpoint = f"{url.rstrip('/')}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": options or {},
    }

    last_err: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            with requests.post(endpoint, json=payload, stream=True, timeout=timeout) as r:
                r.raise_for_status()
                parts = []
                for line in r.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        # Sometimes partials; skip quietly
                        continue
                    # data shape: {"model":..., "created_at":..., "response":"...", "done":bool, ...}
                    chunk = data.get("response", "")
                    if isinstance(chunk, str):
                        parts.append(chunk)
                    if data.get("done") is True:
                        break
                text = "".join(parts).strip()
                if text:
                    return text
                last_err = RuntimeError("Empty response from Ollama stream")
        except Exception as e:
            last_err = e

        if attempt < MAX_RETRIES - 1:
            backoff_sleep(attempt)
        else:
            print(f"[WARN] Max retries reached. Last error: {last_err}")

    return ""  # caller will fall back to original text

def paraphrase_field(text: str, col: str) -> str:
    """
    - Skips non-strings/empty.
    - Checks cache first.
    - Calls Ollama with col-specific prompt via HTTP.
    - Caches result.
    - Returns original text if model returns empty (fail-safe).
    """
    if not isinstance(text, str) or not text.strip():
        return text

    cached = cache_get(col, text)
    if cached:
        return cached

    prompt_template = PROMPTS.get(col, DEFAULT_PROMPT)
    prompt = prompt_template.format(text)

    # gentle throttle
    time.sleep(API_CALL_DELAY_SEC)

    paraphrased = ollama_generate_http(prompt, MODEL_NAME, OLLAMA_URL, OLLAMA_OPTIONS)
    final_text = paraphrased if paraphrased.strip() else text

    cache_put(col, text, final_text)
    return final_text

# =========================
# ------- MAIN FLOW -------
# =========================

def main():
    # Quick sanity: is Ollama reachable?
    try:
        ping = requests.get(f"{OLLAMA_URL.rstrip('/')}/api/tags", timeout=10)
        ping.raise_for_status()
    except Exception as e:
        raise RuntimeError(
            f"Could not reach Ollama at {OLLAMA_URL}. Ensure the daemon is running and the model is pulled."
        ) from e

    init_sqlite_cache()

    print(f"Using Ollama model: {MODEL_NAME}")
    print(f"Loading: {INPUT_XLSX}")
    df = pd.read_excel(INPUT_XLSX)

    cols_present = [c for c in FIELDS_TO_PARAPHRASE if c in df.columns]
    if not cols_present:
        raise RuntimeError("None of the target columns were found in the input file.")

    # Resume from checkpoint if present
    if os.path.exists(CHECKPOINT_PARQUET):
        print(f"Resuming from checkpoint: {CHECKPOINT_PARQUET}")
        try:
            df_ckpt = pd.read_parquet(CHECKPOINT_PARQUET)
            if set(df_ckpt.columns) == set(df.columns) and len(df_ckpt) == len(df):
                df = df_ckpt
            else:
                print("[WARN] Checkpoint shape mismatch. Ignoring checkpoint file.")
        except Exception as e:
            print(f"[WARN] Failed to read checkpoint parquet: {e}")

    processed_rows = load_processed_rows()
    total_rows = len(df)
    print(f"Total rows: {total_rows}")
    print(f"Already processed rows (resume): {len(processed_rows)}")

    remaining_indices = [i for i in range(total_rows) if i not in processed_rows]
    if not remaining_indices:
        print("Nothing to process. Writing final Excel…")
        df.to_excel(OUTPUT_XLSX, index=False, engine="xlsxwriter")
        print(f"Done! Updated file saved as {OUTPUT_XLSX}")
        return

    batches = math.ceil(len(remaining_indices) / BATCH_SIZE)
    pbar_batches = tqdm(range(batches), desc="Batches", unit="batch")

    for b in pbar_batches:
        start = b * BATCH_SIZE
        end = min(start + BATCH_SIZE, len(remaining_indices))
        batch_indices = remaining_indices[start:end]
        if not batch_indices:
            continue

        pbar_rows = tqdm(batch_indices, leave=False, desc=f"Rows {start+1}-{end}", unit="row")

        for i in pbar_rows:
            row = df.iloc[i]
            for col in cols_present:
                val = row[col]
                try:
                    new_val = paraphrase_field(val, col)
                except Exception as e:
                    print(f"[WARN] Row {i} col '{col}' failed: {e}")
                    new_val = val
                df.at[i, col] = new_val
            processed_rows.add(i)

        # checkpoint after each batch
        try:
            df.to_parquet(CHECKPOINT_PARQUET, index=False)
        except Exception as e:
            print(f"[WARN] Failed to write checkpoint parquet: {e}")
        save_processed_rows(processed_rows)
        pbar_batches.set_postfix_str(f"checkpointed @ row {end}")

    print("Writing final Excel…")
    df.to_excel(OUTPUT_XLSX, index=False, engine="xlsxwriter")
    print(f"Done! Updated file saved as {OUTPUT_XLSX}")

if __name__ == "__main__":
    main()
