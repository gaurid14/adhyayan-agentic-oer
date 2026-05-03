import json
import os
from web3 import Web3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ABI_PATH = os.path.join(BASE_DIR, "abi.json")

with open(ABI_PATH) as f:
    ABI = json.load(f)

# ===============================
# GANACHE CONFIG (lazy connect)
# ===============================
GANACHE_URL = "http://127.0.0.1:7545"

CONTRACT_ADDRESS = Web3.to_checksum_address(
    "0xB57cFE7437397dea312380D49991ea91daE2F78C"
)

_w3 = None
_contract = None
_account = None

def _get_contract():
    """Lazily connect to Ganache — only when actually storing scores."""
    global _w3, _contract, _account
    if _contract is None:
        _w3 = Web3(Web3.HTTPProvider(GANACHE_URL))
        if not _w3.is_connected():
            raise ConnectionError(
                "Cannot connect to Ganache at %s. Is it running?" % GANACHE_URL
            )
        _contract = _w3.eth.contract(address=CONTRACT_ADDRESS, abi=ABI)
        _account = _w3.eth.accounts[0]
    return _w3, _contract, _account


# ===============================
# STORE SCORES ON BLOCKCHAIN
# ===============================
def store_scores_on_chain(
        upload_id,
        clarity,
        coherence,
        engagement,
        accuracy,
        completeness
):
    try:
        w3, contract, account = _get_contract()

        tx = contract.functions.storeScores(
            int(upload_id),
            int(clarity * 100),
            int(coherence * 100),
            int(engagement * 100),
            int(accuracy * 100),
            int(completeness * 100)
        ).transact({
            "from": account
        })

        receipt = w3.eth.wait_for_transaction_receipt(tx)

        print("✅ Blockchain stored:", receipt.transactionHash.hex())

        return receipt.transactionHash.hex()
    
    except Exception as e:
        print(f"⚠️ [BLOCKCHAIN] Skipping score storage (non-fatal): {e}")
        return None