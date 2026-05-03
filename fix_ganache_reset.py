import os
import sys
import json
import django
from django.core.management import call_command

def main():
    print("Starting Ganache Reset Fix...")
    
    # 1. Redeploy the smart contract
    print("\n--- Redeploying Smart Contract ---")
    try:
        from solcx import compile_source, install_solc
        from web3 import Web3
    except ImportError:
        print("[ERROR] Missing dependencies. Ensure you are running in the venv.")
        sys.exit(1)

    print("Installing solc 0.8.19...")
    install_solc("0.8.19")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    sol_path = os.path.join(base_dir, "blockchain", "contract", "AdhyayanCertificate.sol")
    abi_out = os.path.join(base_dir, "blockchain", "certificate_abi.json")

    with open(sol_path, "r", encoding="utf-8") as f:
        source = f.read()

    print("Compiling AdhyayanCertificate.sol...")
    compiled = compile_source(source, solc_version="0.8.19")
    contract_id, contract_interface = next(
        (k, v) for k, v in compiled.items() if "AdhyayanCertificate" in k
    )

    abi = contract_interface["abi"]
    bytecode = contract_interface["bin"]

    w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:7545"))
    if not w3.is_connected():
        print("[ERROR] Cannot connect to Ganache at http://127.0.0.1:7545. Is it running?")
        sys.exit(1)

    deployer = w3.eth.accounts[0]
    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx_hash = Contract.constructor().transact({"from": deployer})
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    address = receipt.contractAddress

    print(f"Contract deployed at: {address}")

    with open(abi_out, "w", encoding="utf-8") as f:
        json.dump(abi, f, indent=2)

    # 2. Update .env
    print("\n--- Updating .env ---")
    env_path = os.path.join(base_dir, ".env")
    with open(env_path, "r", encoding="utf-8") as f:
        env_lines = f.readlines()

    with open(env_path, "w", encoding="utf-8") as f:
        for line in env_lines:
            if line.startswith("CERTIFICATE_CONTRACT_ADDRESS="):
                f.write(f"CERTIFICATE_CONTRACT_ADDRESS={address}\n")
            else:
                f.write(line)
    print(f"Updated CERTIFICATE_CONTRACT_ADDRESS in .env to {address}")

    # 3. Setup Django and Clear old certificates
    print("\n--- Clearing old database records ---")
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'oer.settings')
    django.setup()

    from accounts.models import BlockchainCertificate
    count, _ = BlockchainCertificate.objects.all().delete()
    print(f"Deleted {count} old certificate records from the database.")

    # 4. Mint new certificates
    print("\n--- Minting missing certificates ---")
    call_command('mint_missing_certs')
    print("\nFix complete! You can now verify certificates on the platform.")

if __name__ == "__main__":
    main()
