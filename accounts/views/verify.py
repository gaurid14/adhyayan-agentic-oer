from django.shortcuts import render
from django.http import Http404
from accounts.models import BlockchainCertificate
from blockchain.services.certificate_service import verify_certificate


def verify_certificate_view(request, token_id):
    """
    Publicly accessible view to verify a blockchain certificate.
    Fetches the source of truth directly from the Ganache EVM.
    """
    # 1. First check if we have an off-chain record to ensure the URL is valid
    try:
        cert_record = BlockchainCertificate.objects.get(token_id=token_id)
    except BlockchainCertificate.DoesNotExist:
        # We can still attempt to query the chain directly if someone passes
        # an arbitrary token ID, but for our app, we'll verify it first.
        cert_record = None

    # 2. Query the blockchain for the immutable truth
    chain_data = verify_certificate(token_id)

    if not chain_data.get("exists"):
        error_msg = chain_data.get("error", "Unknown error.")
        # Give user-friendly messages for the two main failure cases
        if "Cannot connect" in error_msg or "ConnectionError" in error_msg or "connect" in error_msg.lower():
            friendly_error = (
                "⚠️ The Adhyayan blockchain node is currently unavailable. "
                "Please ensure Ganache is running and try again."
            )
        elif "not set" in error_msg.lower() or "contract" in error_msg.lower():
            friendly_error = (
                "⚠️ The smart contract address is not configured. "
                "Please contact the platform administrator."
            )
        else:
            friendly_error = f"This Certificate (Token #{token_id}) does not exist on the Adhyayan Blockchain."
        return render(request, "verify_certificate.html", {"error": friendly_error})

    # 3. Format the data for the beautiful HTML view
    from django.conf import settings
    from datetime import datetime, timezone as dt_tz
    contract_address = getattr(settings, "CERTIFICATE_CONTRACT_ADDRESS", "0x...")

    # Convert blockchain Unix timestamp to human-readable string
    issued_at_unix = chain_data["issued_at"]
    try:
        issued_at_str = datetime.fromtimestamp(issued_at_unix, tz=dt_tz.utc).strftime("%d %B %Y, %I:%M %p UTC")
    except Exception:
        issued_at_str = str(issued_at_unix)

    context = {
        "token_id": chain_data["token_id"],
        "recipient_name": chain_data["recipient_name"],
        "course_name": chain_data["course_name"],
        "issue_type_label": chain_data["issue_type_label"],
        "issued_at": issued_at_unix,          # raw unix int — for JS data-attribute
        "issued_at_str": issued_at_str,        # human-readable fallback
        "tx_hash": cert_record.tx_hash if cert_record else "Unknown Hash",
        "blockchain_verified": True,
        "contract_address": contract_address
    }

    return render(request, "verify_certificate.html", context)
