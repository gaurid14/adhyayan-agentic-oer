# Test script to verify the ContentScore and certificate status
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "OER.settings")
django.setup()

from accounts.models import ContentScore, BlockchainCertificate

print("Checking ContentScore 50...")
try:
    score = ContentScore.objects.get(id=50)
    print(f"ContentScore 50 found. Upload ID: {score.upload_id}")
    print(f"is_best: {score.is_best}")
    print(f"accuracy: {score.accuracy}, clarity: {score.clarity}")
    
    cert = BlockchainCertificate.objects.filter(
        chapter_id=score.upload.chapter_id, 
        user_id=score.upload.contributor_id
    ).first()
    if cert:
        print(f"Certificate exists! Token ID: {cert.token_id}")
    else:
        print("No certificate minted yet for this upload/chapter.")
except Exception as e:
    print(f"Error: {e}")
