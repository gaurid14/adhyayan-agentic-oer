# services/evaluation_finalize_service.py

from asgiref.sync import sync_to_async
from accounts.models import ContentScore
from blockchain.services.evaluation_scores import store_scores_on_chain


async def finalize_evaluation(upload_id: int):

    # ORM must be wrapped
    scores = await sync_to_async(
        ContentScore.objects.get
    )(upload_id=upload_id)

    # blockchain write
    await sync_to_async(store_scores_on_chain)(
        upload_id=upload_id,
        clarity=scores.clarity,
        coherence=scores.coherence,
        engagement=scores.engagement,
        accuracy=scores.accuracy,
        completeness=scores.completeness,
    )