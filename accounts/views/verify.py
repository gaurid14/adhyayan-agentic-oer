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
        return render(request, "verify_certificate.html", {
            "error": "This Certificate Token ID does not exist on the Adhyayan Blockchain."
        })

    # 3. Format the data for the beautiful HTML view
    from django.conf import settings
    contract_address = getattr(settings, "CERTIFICATE_CONTRACT_ADDRESS", "0x...")

    context = {
        "token_id": chain_data["token_id"],
        "recipient_name": chain_data["recipient_name"],
        "course_name": chain_data["course_name"],
        "issue_type_label": chain_data["issue_type_label"],
        "issued_at": chain_data["issued_at"],
        "tx_hash": cert_record.tx_hash if cert_record else "Unknown Hash",
        "blockchain_verified": True,
        "contract_address": contract_address
    }

    return render(request, "verify_certificate.html", context)
