import json
import os
from web3 import Web3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ABI_PATH = os.path.join(BASE_DIR, "abi.json")

with open(ABI_PATH) as f:
    ABI = json.load(f)

# ===============================
# GANACHE CONNECTION
# ===============================
GANACHE_URL = "http://127.0.0.1:7545"

w3 = Web3(Web3.HTTPProvider(GANACHE_URL))

# contract address from remix
CONTRACT_ADDRESS = Web3.to_checksum_address(
    "0x20Ed59F79F4D4afeb61EAe08039C92E3FEC856fa"
)

contract = w3.eth.contract(
    address=CONTRACT_ADDRESS,
    abi=ABI
)

ACCOUNT = w3.eth.accounts[0]


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

    tx = contract.functions.storeScores(
        int(upload_id),
        int(clarity * 100),
        int(coherence * 100),
        int(engagement * 100),
        int(accuracy * 100),
        int(completeness * 100)
    ).transact({
        "from": ACCOUNT
    })

    receipt = w3.eth.wait_for_transaction_receipt(tx)

    print("✅ Blockchain stored:", receipt.transactionHash.hex())

    return receipt.transactionHash.hex()