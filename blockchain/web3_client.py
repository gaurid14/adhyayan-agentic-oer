from web3 import Web3
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# -------------------
# CONNECT BLOCKCHAIN
# -------------------
w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:7545"))

print("Connected:", w3.is_connected())

# -------------------
# LOAD ABI FILE
# -------------------
abi_path = os.path.join(BASE_DIR, "abi.json")

with open(abi_path, "r") as f:
    ABI = json.load(f)

# -------------------
# CONTRACT
# -------------------
CONTRACT_ADDRESS = Web3.to_checksum_address(
    "0x20Ed59F79F4D4afeb61EAe08039C92E3FEC856fa"
)

contract = w3.eth.contract(
    address=CONTRACT_ADDRESS,
    abi=ABI
)

ACCOUNT = w3.eth.accounts[0]