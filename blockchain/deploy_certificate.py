"""
blockchain/deploy_certificate.py
----------------------------------
One-shot deployment script for AdhyayanCertificate.sol.

Usage (from project root with venv active):
    python blockchain/deploy_certificate.py

Requirements:
    pip install py-solc-x web3

The script will:
1. Auto-install the correct solc version.
2. Compile AdhyayanCertificate.sol.
3. Deploy it to the local Ganache node at http://127.0.0.1:7545.
4. Print the deployed contract address and ABI.
5. Save the ABI to blockchain/certificate_abi.json.
6. IMPORTANT: Copy the printed contract address into your .env file as
   CERTIFICATE_CONTRACT_ADDRESS=0x...
"""

import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOL_PATH = os.path.join(BASE_DIR, "contract", "AdhyayanCertificate.sol")
ABI_OUT  = os.path.join(BASE_DIR, "certificate_abi.json")

try:
    from solcx import compile_source, install_solc
    from web3 import Web3
except ImportError:
    print("[ERROR] Missing dependencies. Run: pip install py-solc-x web3")
    sys.exit(1)

# ── 1. Install Solidity compiler ────────────────────────────────────────────
print("Installing solc 0.8.19...")
install_solc("0.8.19")

# ── 2. Read + compile the contract ──────────────────────────────────────────
with open(SOL_PATH, "r", encoding="utf-8") as f:
    source = f.read()

print("Compiling AdhyayanCertificate.sol...")
compiled = compile_source(source, solc_version="0.8.19")
contract_id, contract_interface = next(
    (k, v) for k, v in compiled.items() if "AdhyayanCertificate" in k
)

abi      = contract_interface["abi"]
bytecode = contract_interface["bin"]
print(f"  → Compiled: {contract_id}")

# ── 3. Connect to Ganache ────────────────────────────────────────────────────
w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:7545"))
if not w3.is_connected():
    print("[ERROR] Cannot connect to Ganache at http://127.0.0.1:7545. Is it running?")
    sys.exit(1)

deployer = w3.eth.accounts[0]
print(f"  → Connected. Deploying from account: {deployer}")

# ── 4. Deploy ────────────────────────────────────────────────────────────────
Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
tx_hash  = Contract.constructor().transact({"from": deployer})
receipt  = w3.eth.wait_for_transaction_receipt(tx_hash)
address  = receipt.contractAddress

print(f"\n✅ Contract deployed at: {address}")
print(f"   Transaction hash: {tx_hash.hex()}")

# ── 5. Save ABI ──────────────────────────────────────────────────────────────
with open(ABI_OUT, "w", encoding="utf-8") as f:
    json.dump(abi, f, indent=2)
print(f"   ABI saved to: {ABI_OUT}")

print("\n" + "="*60)
print("ACTION REQUIRED:")
print(f"  Add to your .env file: CERTIFICATE_CONTRACT_ADDRESS={address}")
print("="*60)
