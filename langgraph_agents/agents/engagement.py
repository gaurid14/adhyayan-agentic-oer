import json

from asgiref.sync import sync_to_async
from langchain.tools import tool

from accounts.models import ContentScore, UploadCheck, Assessment
from langgraph_agents.services.drive_service import GoogleDriveAuthService
from langgraph_agents.services.gemini_service import llm
from langgraph_agents.services.pdf_service import download_and_read_pdf
import tempfile
from googleapiclient.http import MediaIoBaseDownload


from langgraph_agents.services.video_service import transcribe_audio_or_video


def extract_all_pdf_texts(folder_id):
    service = GoogleDriveAuthService.get_service()
    results = service.files().list(
        q=f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false",
        fields="files(id, name)"
    ).execute()

    pdf_texts = []
    for f in results.get("files", []):
        content = download_and_read_pdf(f["id"])
        pdf_texts.append(content)
    return pdf_texts


def extract_all_video_transcripts(folder_id):
    """
    Extracts transcripts from all video files in a Drive folder.
    Downloads each video temporarily to transcribe with Whisper.
    """
    service = GoogleDriveAuthService.get_service()
    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id, name, mimeType)"
    ).execute()

    transcripts = []

    for f in results.get("files", []):
        if f["mimeType"].startswith("video/"):
            print(f"[INFO] Processing video: {f['name']}")

            # Create a temporary file
            import os
            import tempfile

            # Create temp file WITHOUT keeping it open
            tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
            tmp_path = tmp_file.name
            tmp_file.close()   # IMPORTANT: close file handle before ffmpeg reads it

            # Download video from Google Drive
            request = service.files().get_media(fileId=f["id"])
            with open(tmp_path, "wb") as out:
                downloader = MediaIoBaseDownload(out, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()

            # Now transcribe using Whisper
            state = {"file_path": tmp_path}
            state = transcribe_audio_or_video(state)
            transcripts.append(state.get("transcript", ""))

            # Delete file afterwards
            os.remove(tmp_path)


    return transcripts


def extract_all_pdf_texts_recursive(folder_id):
    """
    Recursively scans a folder and ALL its subfolders for PDFs.
    Returns a list of all extracted text.
    """
    service = GoogleDriveAuthService.get_service()

    pdf_texts = []

    def scan_folder(folder_id):
        # Fetch all files & subfolders
        results = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id, name, mimeType)"
        ).execute()

        for f in results.get("files", []):
            mime = f["mimeType"]

            # If PDF -> extract content
            if mime == "application/pdf":
                print(f"[INFO] Reading PDF: {f['name']}")
                content = download_and_read_pdf(f["id"])
                if content:
                    pdf_texts.append(content)

            # If folder -> scan it recursively
            elif mime == "application/vnd.google-apps.folder":
                print(f"[INFO] Entering folder: {f['name']}")
                scan_folder(f["id"])

    scan_folder(folder_id)
    return pdf_texts


def extract_all_video_transcripts_recursive(folder_id):
    """
    Recursively scans all folders and extracts transcripts from all video files.
    """
    service = GoogleDriveAuthService.get_service()
    transcripts = []

    def scan_folder(folder_id):
        results = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id, name, mimeType)"
        ).execute()

        for f in results.get("files", []):
            mime = f["mimeType"]

            # If video → download + transcribe
            if mime.startswith("video/"):
                print(f"[INFO] Processing video: {f['name']}")

                import os
                import tempfile

                # Create temp file WITHOUT keeping it open
                tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                tmp_path = tmp_file.name
                tmp_file.close()   # IMPORTANT: close file handle before ffmpeg reads it

                # Download video from Google Drive
                request = service.files().get_media(fileId=f["id"])
                with open(tmp_path, "wb") as out:
                    downloader = MediaIoBaseDownload(out, request)
                    done = False
                    while not done:
                        status, done = downloader.next_chunk()

                # Now transcribe using Whisper
                state = {"file_path": tmp_path}
                state = transcribe_audio_or_video(state)
                transcripts.append(state.get("transcript", ""))

                # Delete file afterwards
                os.remove(tmp_path)

# If folder → recursive scan
            elif mime == "application/vnd.google-apps.folder":
                scan_folder(f["id"])

    scan_folder(folder_id)
    return transcripts



async def analyze_engagement_with_gemini(content: str) -> dict:
    """
    Uses Gemini to count engagement elements in content.
    """
    prompt = f"""
    You are an educational content evaluator.

    Analyze the following material and return the COUNT of:
    - Case studies (explicit or implied)
    - Assessments (questions, exercises, or activities)
    - Scenario cues or examples (real-world, what-if, role-play)

    Respond ONLY in pure JSON:
    {{
      "case_studies": <int>,
      "assessments": <int>,
      "scenario_cues": <int>
    }}

    Content:
    {content}
    """

    response = llm.invoke(prompt)
    try:
        return json.loads(response.content)
    except Exception as e:
        print("[ERROR] Gemini JSON parsing failed:", e)
        return {"case_studies": 0, "assessments": 0, "scenario_cues": 0}

# @tool
# async def evaluate_engagement(contributor_id: int, chapter_id: int, drive_folders: dict, **kwargs) -> dict:
#     """
#     Evaluates the engagement level of uploaded content using Gemini + assessment DB check.
#     """
#
#     # ORM in async → wrap it
#     upload = await sync_to_async(
#         lambda: UploadCheck.objects.filter(
#             contributor_id=contributor_id, chapter_id=chapter_id
#         ).order_by('-timestamp').first()
#     )()
#
#     if not upload:
#         return {"status": "no_upload_found"}
#
#     # Get or create ContentScore entry
#     score_obj, _ = ContentScore.objects.get_or_create(upload=upload)
#
#     # Extract content from PDFs and videos
#     pdf_texts = extract_all_pdf_texts(drive_folders.get("pdf"))
#     video_texts = extract_all_video_transcripts(drive_folders.get("videos"))
#     full_text = "\n\n".join(pdf_texts + video_texts)
#
#     if not full_text.strip():
#         return {"status": "no_content_found"}
#
#     # Run Gemini analysis
#     gemini_result = await analyze_engagement_with_gemini(full_text)
#     case_studies = gemini_result["case_studies"]
#     assessments = gemini_result["assessments"]
#     scenario_cues = gemini_result["scenario_cues"]
#
#     # Check if contributor uploaded assessments for this chapter
#     has_assessment = Assessment.objects.filter(
#         chapter_id=chapter_id,
#         course_id=upload.chapter.course_id,
#         contributor_id=contributor_id
#     ).exists()
#
#     # Compute engagement score (weighted 0–10)
#     engagement_score = (
#             (case_studies * 2) +
#             (scenario_cues * 1.5) +
#             (assessments * 1.5) +
#             (5 if has_assessment else 0)
#     )
#
#     # Cap the score at 10
#     engagement_score = min(10, round(engagement_score, 2))
#
#     # Save the score
#     score_obj.engagement = engagement_score
#     score_obj.save()
#
#     # Check if all four parameters are filled → mark evaluation complete
#     if all([
#         score_obj.completeness,
#         score_obj.clarity,
#         score_obj.accuracy,
#         score_obj.engagement
#     ]):
#         upload.evaluation_status = True
#         upload.save()
#
#     return {
#         "status": "engagement_evaluated",
#         "details": {
#             "case_studies": case_studies,
#             "scenario_cues": scenario_cues,
#             "assessments_found": assessments,
#             "assessment_uploaded": has_assessment
#         },
#         "score": engagement_score
#     }


from asgiref.sync import sync_to_async
from langchain.tools import tool

@tool
async def evaluate_engagement(contributor_id: int, chapter_id: int, drive_folders: dict, **kwargs) -> dict:
    """
    Evaluates the engagement level of uploaded content using Gemini + assessment DB check.
    """

    # ✅ ORM in async → wrap it
    upload = await sync_to_async(
        lambda: UploadCheck.objects.filter(
            contributor_id=contributor_id, chapter_id=chapter_id
        ).order_by('-timestamp').first()
    )()

    if not upload:
        return {"status": "no_upload_found"}

    # ✅ Wrap get_or_create
    score_obj, _ = await sync_to_async(
        lambda: ContentScore.objects.get_or_create(upload=upload)
    )()

    # ✅ If your PDF / video extractors are synchronous — wrap them too
    pdf_texts = await sync_to_async(extract_all_pdf_texts_recursive)(drive_folders.get("pdf"))
    video_texts = await sync_to_async(extract_all_video_transcripts_recursive)(drive_folders.get("videos"))
    full_text = "\n\n".join(pdf_texts + video_texts)

    if not full_text.strip():
        return {"status": "no_content_found"}

    # ✅ Gemini analysis (already async)
    gemini_result = await analyze_engagement_with_gemini(full_text)
    case_studies = gemini_result["case_studies"]
    assessments = gemini_result["assessments"]
    scenario_cues = gemini_result["scenario_cues"]

    # ✅ ORM existence check
    has_assessment = await sync_to_async(
        lambda: Assessment.objects.filter(
            chapter_id=chapter_id,
            course_id=upload.chapter.course_id,
            contributor_id=contributor_id
        ).exists()
    )()

    # Compute engagement score (weighted 0–10)
    engagement_score = (
            (case_studies * 2) +
            (scenario_cues * 1.5) +
            (assessments * 1.5) +
            (5 if has_assessment else 0)
    )

    engagement_score = min(10, round(engagement_score, 2))

    # ✅ Save the score safely
    await sync_to_async(lambda: _save_score(score_obj, engagement_score))()

    # ✅ Check if all parameters filled and mark complete
    # await sync_to_async(lambda: _finalize_upload(upload, score_obj))()

    return {
        "status": "engagement_evaluated",
        "details": {
            "case_studies": case_studies,
            "scenario_cues": scenario_cues,
            "assessments_found": assessments,
            "assessment_uploaded": has_assessment
        },
        "score": engagement_score
    }


# Helper functions (run synchronously)
def _save_score(score_obj, engagement_score):
    score_obj.engagement = engagement_score
    score_obj.save()


# def _finalize_upload(upload, score_obj):
#     if all([
#         score_obj.completeness,
#         score_obj.clarity,
#         score_obj.accuracy,
#         score_obj.engagement
#     ]):
#         upload.evaluation_status = True
#         upload.save()
