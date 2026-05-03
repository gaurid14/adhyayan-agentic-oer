"""
blockchain/services/decision_audit.py

Blockchain service for the Approval Audit Trail.

Mirrors the pattern established in evaluation_scores.py:
  - Lazy Ganache connection (no crash at import time)
  - Graceful fallback if blockchain is unreachable
  - Simple store + read API

Every Decision Agent run that produces a DecisionRun record
also pushes an immutable copy here, returning a tx_hash
that proves the decision was recorded at a specific time.
"""

import json
import logging
import os
from typing import Optional, Tuple

from web3 import Web3

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# PATHS & CONFIG
# -----------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECISION_ABI_PATH = os.path.join(BASE_DIR, "decision_abi.json")

GANACHE_URL = "http://127.0.0.1:7545"

# ⚠️  UPDATE THIS after deploying DecisionAudit.sol on Ganache via Remix/Truffle
DECISION_CONTRACT_ADDRESS = Web3.to_checksum_address(
    "0x05D27eF76e52f2399E13792cF83136b3F8e65d64"   # updated after deploy
)

# Status codes matching DecisionAudit.sol
STATUS_APPROVED      = 1
STATUS_REJECTED      = 2
STATUS_NO_CANDIDATES = 3

_STATUS_MAP = {
    "ok":            STATUS_APPROVED,
    "approved":      STATUS_APPROVED,
    "rejected":      STATUS_REJECTED,
    "no_candidates": STATUS_NO_CANDIDATES,
    "no_uploads":    STATUS_NO_CANDIDATES,
}


# -----------------------------------------------------------------------
# LAZY CONTRACT LOADER  (same pattern as evaluation_scores.py)
# -----------------------------------------------------------------------
_w3 = None
_contract = None
_account = None


def _get_decision_contract():
    """Lazily connect to Ganache and load the DecisionAudit contract."""
    global _w3, _contract, _account

    if _contract is not None:
        return _w3, _contract, _account

    if not os.path.exists(DECISION_ABI_PATH):
        raise FileNotFoundError(
            f"DecisionAudit ABI not found at {DECISION_ABI_PATH}. "
            "Please compile DecisionAudit.sol and place the ABI there."
        )

    with open(DECISION_ABI_PATH, "r") as f:
        abi = json.load(f)

    _w3 = Web3(Web3.HTTPProvider(GANACHE_URL))

    if not _w3.is_connected():
        raise ConnectionError(
            f"Cannot connect to Ganache at {GANACHE_URL}. Is it running?"
        )

    _contract = _w3.eth.contract(address=DECISION_CONTRACT_ADDRESS, abi=abi)
    _account = _w3.eth.accounts[0]

    return _w3, _contract, _account


# -----------------------------------------------------------------------
# STORE DECISION ON CHAIN
# -----------------------------------------------------------------------
def store_decision_on_chain(
    chapter_id: int,
    upload_id: int,
    composite_score: float,
    status: str,
) -> Optional[str]:
    """
    Push a decision record onto the blockchain.

    Parameters
    ----------
    chapter_id      : ID of the chapter.
    upload_id       : ID of the selected upload (0 if none).
    composite_score : Final composite score (will be stored ×100).
    status          : "ok" | "approved" | "rejected" | "no_candidates" | "no_uploads".

    Returns
    -------
    tx_hash : str | None
        The transaction hash as a hex string, or None if the
        blockchain is unreachable (graceful fallback).
    """
    try:
        w3, contract, account = _get_decision_contract()

        status_code = _STATUS_MAP.get(status, STATUS_NO_CANDIDATES)
        score_int   = int((composite_score or 0) * 100)

        tx = contract.functions.recordDecision(
            int(chapter_id),
            int(upload_id),
            score_int,
            status_code,
        ).transact({"from": account})

        receipt = w3.eth.wait_for_transaction_receipt(tx)
        tx_hash = receipt.transactionHash.hex()

        print(f"🔗 [BLOCKCHAIN] Decision recorded on-chain: tx_hash={tx_hash}")
        print(f"   chapter_id={chapter_id} upload_id={upload_id} score={composite_score} status={status}")

        return tx_hash

    except FileNotFoundError as e:
        print(f"⚠️  [BLOCKCHAIN] ABI missing — skipping on-chain audit: {e}")
        return None

    except ConnectionError:
        print("⚠️  [BLOCKCHAIN] Ganache is offline — decision saved to DB only (no on-chain audit)")
        return None

    except Exception as e:
        # Non-destructive: log the error but never crash the decision flow
        print(f"⚠️  [BLOCKCHAIN] On-chain audit failed (non-fatal): {e}")
        logger.warning("[decision_audit] store_decision_on_chain failed: %s", e)
        return None


# -----------------------------------------------------------------------
# READ DECISION FROM CHAIN  (for verification)
# -----------------------------------------------------------------------
def get_decision_from_chain(
    chapter_id: int,
    index: int = -1,
) -> Optional[dict]:
    """
    Read a decision record back from the blockchain.

    Parameters
    ----------
    chapter_id : Chapter to look up.
    index      : Which decision (0-based). -1 means the latest.

    Returns
    -------
    dict with selectedUploadId, compositeScore, status, timestamp
    or None if unavailable.
    """
    try:
        _, contract, _ = _get_decision_contract()

        count = contract.functions.getDecisionCount(int(chapter_id)).call()
        if count == 0:
            return None

        if index < 0:
            index = count - 1

        result = contract.functions.getDecision(int(chapter_id), int(index)).call()

        return {
            "selectedUploadId": result[0],
            "compositeScore":   result[1] / 100.0,
            "status":           result[2],
            "timestamp":        result[3],
        }

    except Exception as e:
        logger.warning("[decision_audit] get_decision_from_chain failed: %s", e)
        return None


def get_decision_count(chapter_id: int) -> int:
    """Return how many on-chain decisions exist for a chapter."""
    try:
        _, contract, _ = _get_decision_contract()
        return contract.functions.getDecisionCount(int(chapter_id)).call()
    except Exception:
        return 0
