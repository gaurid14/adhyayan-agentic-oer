"""
gemini_service.py
=================
Provides a drop-in `llm` object that automatically rotates through
multiple GEMINI_API_KEYs when a key is exhausted (429) or invalid (400).

Every other file can continue to do:
    from langgraph_agents.services.gemini_service import llm
    response = llm.invoke(prompt)

No changes required anywhere else.
"""

import os
import re
import sys
import threading
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

# ── Collect ALL uncommented GEMINI_API_KEY values from .env ──────────────────
def _load_all_api_keys() -> list[str]:
    """
    Parse the .env file and collect every uncommented GEMINI_API_KEY value.
    Falls back to the single environment variable if parsing fails.
    """
    keys: list[str] = []

    # Try to read the .env file directly to find ALL keys (commented-out ones
    # are skipped; only lines where GEMINI_API_KEY is the first non-whitespace
    # token are used).
    env_paths = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]

    for env_path in env_paths:
        if not os.path.isfile(env_path):
            continue
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # Skip blank lines and comments
                    if not line or line.startswith("#"):
                        continue
                    # Match: GEMINI_API_KEY=<value>  or  GEMINI_API_KEY="<value>"
                    m = re.match(r'^GEMINI_API_KEY\s*=\s*["\']?([^"\'#\s]+)', line)
                    if m:
                        key = m.group(1).strip()
                        if key and key not in keys:
                            keys.append(key)
            if keys:
                break  # found keys in this file, no need to try the next path
        except Exception:
            continue

    # Fallback: at minimum use the env var that dotenv already loaded
    if not keys:
        single = os.getenv("GEMINI_API_KEY")
        if single:
            keys.append(single)

    return keys


_ALL_KEYS = _load_all_api_keys()
if not _ALL_KEYS:
    raise ValueError("No GEMINI_API_KEY found in .env file")

MODEL_NAME = "gemini-2.5-flash-lite"

# File to persist the last working key index across server restarts
_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".gemini_key_index"
)


def _load_saved_index() -> int:
    """Load the last working key index from disk."""
    try:
        if os.path.isfile(_STATE_FILE):
            with open(_STATE_FILE, "r") as f:
                return int(f.read().strip())
    except Exception:
        pass
    return 0


def _save_index(index: int):
    """Save the current key index to disk."""
    try:
        with open(_STATE_FILE, "w") as f:
            f.write(str(index))
    except Exception:
        pass


class KeyRotatingLLM:
    """
    A thin wrapper around ChatGoogleGenerativeAI that automatically
    rotates API keys on quota/invalid-key errors.

    It exposes the same .invoke() interface so all existing code
    works without modification.

    Key features:
    - max_retries=0 disables LangChain's internal exponential backoff,
      so 429 errors propagate instantly to our rotation logic.
    - The current key index is persisted to .gemini_key_index so that
      server restarts skip already-exhausted keys.
    """

    def __init__(self, keys: list[str], model: str):
        self._keys = list(keys)
        self._model = model
        self._lock = threading.Lock()

        # Resume from the last working key (survives server restarts)
        saved = _load_saved_index()
        self._index = saved if saved < len(self._keys) else 0

        self._llm = self._make_llm(self._keys[self._index])
        print(
            f"🔑 [KEY-ROTATION] Initialized with {len(self._keys)} API key(s). "
            f"Starting from key #{self._index + 1}.",
            file=sys.stderr
        )

    def _make_llm(self, api_key: str) -> ChatGoogleGenerativeAI:
        # max_retries=0 prevents LangChain from doing its own 2s/4s/8s/16s/32s
        # exponential backoff on 429 errors. We handle retries ourselves by
        # rotating to the next key instantly.
        return ChatGoogleGenerativeAI(
            model=self._model,
            api_key=api_key,
            max_retries=0,
        )

    def _rotate_key(self) -> bool:
        """Advance to the next key. Returns False if all keys exhausted."""
        with self._lock:
            next_index = self._index + 1
            if next_index >= len(self._keys):
                return False
            self._index = next_index
            self._llm = self._make_llm(self._keys[self._index])
            _save_index(self._index)  # persist so restarts skip dead keys
            print(
                f"🔄 [KEY-ROTATION] Switched to API key #{self._index + 1}/{len(self._keys)}.",
                file=sys.stderr
            )
            return True

    def _is_key_error(self, exc: Exception) -> bool:
        """Check if the exception is a quota or invalid-key error."""
        msg = str(exc).lower()
        return any(indicator in msg for indicator in [
            "429",
            "resource has been exhausted",
            "quota",
            "api_key_invalid",
            "api key not found",
            "api key not valid",
        ])

    def invoke(self, *args, **kwargs):
        """Drop-in replacement for ChatGoogleGenerativeAI.invoke()."""
        while True:
            try:
                result = self._llm.invoke(*args, **kwargs)
                # If successful, persist this key as the last working one
                _save_index(self._index)
                return result
            except Exception as e:
                if self._is_key_error(e):
                    key_num = self._index + 1
                    print(
                        f"⚠️ [KEY-ROTATION] Key #{key_num} exhausted. Rotating...",
                        file=sys.stderr
                    )
                    if self._rotate_key():
                        continue  # retry with the next key immediately
                    else:
                        print("🛑 [KEY-ROTATION] All API keys exhausted!", file=sys.stderr)
                        raise RuntimeError(
                            f"All {len(self._keys)} Gemini API keys are exhausted. "
                            f"Please add more keys to your .env file."
                        ) from e
                else:
                    raise  # re-raise non-key errors immediately

    # Forward attribute access to the underlying LLM so that any code
    # checking llm.model_name or similar still works.
    def __getattr__(self, name):
        return getattr(self._llm, name)


# ── The single global instance every file imports ────────────────────────────
llm = KeyRotatingLLM(keys=_ALL_KEYS, model=MODEL_NAME)
