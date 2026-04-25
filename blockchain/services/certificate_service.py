"""
blockchain/services/certificate_service.py
-------------------------------------------
Python bridge for interacting with the AdhyayanCertificate smart contract.

Exposes two core functions:
  - mint_certificate(recipient_name, course_name, issue_type) -> dict
  - verify_certificate(token_id) -> dict

Reads configuration from Django settings (which reads from .env):
  CERTIFICATE_CONTRACT_ADDRESS = "0x..."
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABI_PATH = os.path.join(BASE_DIR, "certificate_abi.json")

ISSUE_TYPE_STUDENT     = 0
ISSUE_TYPE_CONTRIBUTOR = 1


def _get_contract():
    """Lazy-load Web3 + contract. Returns (w3, contract, deployer_account)."""
    from web3 import Web3
    from django.conf import settings

    ganache_url = getattr(settings, "GANACHE_URL", "http://127.0.0.1:7545")
    contract_address = getattr(settings, "CERTIFICATE_CONTRACT_ADDRESS", None)

    if not contract_address:
        raise ValueError(
            "CERTIFICATE_CONTRACT_ADDRESS is not set in settings / .env. "
            "Run blockchain/deploy_certificate.py first."
        )

    w3 = Web3(Web3.HTTPProvider(ganache_url))
    if not w3.is_connected():
        raise ConnectionError(f"Cannot connect to blockchain node at {ganache_url}. Is Ganache running?")

    with open(ABI_PATH, "r", encoding="utf-8") as f:
        abi = json.load(f)

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(contract_address),
        abi=abi,
    )
    account = w3.eth.accounts[0]
    return w3, contract, account


def mint_certificate(recipient_name: str, course_name: str, issue_type: int) -> dict:
    """
    Mint a certificate on-chain.

    Args:
        recipient_name: Full name of the recipient.
        course_name:    Course or chapter name.
        issue_type:     ISSUE_TYPE_STUDENT (0) or ISSUE_TYPE_CONTRIBUTOR (1).

    Returns:
        {
            "success": True,
            "token_id": int,
            "tx_hash": str,
        }
    """
    try:
        w3, contract, account = _get_contract()

        tx_hash = contract.functions.mintCertificate(
            recipient_name,
            course_name,
            issue_type,
        ).transact({"from": account})

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

        # Decode the CertificateMinted event to get the token_id
        events = contract.events.CertificateMinted().process_receipt(receipt)
        token_id = events[0]["args"]["tokenId"] if events else None

        logger.info(
            "[Certificate] Minted token_id=%s for '%s' (%s) tx=%s",
            token_id, recipient_name, "Student" if issue_type == 0 else "Contributor",
            tx_hash.hex()
        )

        return {"success": True, "token_id": token_id, "tx_hash": tx_hash.hex()}

    except Exception as e:
        logger.error("[Certificate] Minting failed for '%s': %s", recipient_name, e)
        return {"success": False, "error": str(e)}


def verify_certificate(token_id: int) -> dict:
    """
    Fetch certificate data directly from the blockchain.

    Args:
        token_id: The unique token ID of the certificate.

    Returns:
        {
            "exists": True,
            "token_id": int,
            "recipient_name": str,
            "course_name": str,
            "issue_type": int,        # 0=Student, 1=Contributor
            "issue_type_label": str,  # "Student" | "Contributor"
            "issued_at": int,         # Unix timestamp
        }
        OR {"exists": False, "error": str}
    """
    try:
        _, contract, _ = _get_contract()
        exists = contract.functions.certificateExists(token_id).call()

        if not exists:
            return {"exists": False, "error": "Certificate not found on blockchain."}

        recipient_name, course_name, issue_type, issued_at = (
            contract.functions.getCertificate(token_id).call()
        )

        return {
            "exists": True,
            "token_id": token_id,
            "recipient_name": recipient_name,
            "course_name": course_name,
            "issue_type": issue_type,
            "issue_type_label": "Student" if issue_type == 0 else "Contributor",
            "issued_at": issued_at,
        }

    except Exception as e:
        logger.error("[Certificate] Verification failed for token_id=%s: %s", token_id, e)
        return {"exists": False, "error": str(e)}
