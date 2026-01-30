import os
import io
import tempfile
import traceback
from typing import Dict, Any, Optional
from asgiref.sync import sync_to_async

from mcp.server.fastmcp import FastMCP
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(BASE_DIR)
print("✅ MCP SERVER FILE LOADED:", __file__,file=sys.stderr, flush=True)

# Django setup (important when MCP server runs outside manage.py)
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oer.settings")  # <-- change if project name different
django.setup()

from django.conf import settings
from django.utils import timezone

# Existing services
from langgraph_agents.services.drive_service import GoogleDriveAuthService
from langgraph_agents.services.gemini_service import llm
from langgraph_agents.services.pdf_service import download_and_read_pdf

# Whisper import (for better transcript quality)
import whisper
import torch

# DB models
from accounts.models import UploadCheck, Chapter, ContentCheck, ContentScore, ReleasedContent

# Create MCP Server
mcp = FastMCP("adhyayan-mcp-server")

# --------------------------------------
# DRIVE TOOLS
# --------------------------------------

@mcp.tool()
def drive_list_files(folder_id: str, page_size: int = 50, recursive: bool = False) -> Dict[str, Any]:
    """
    List files inside a Google Drive folder.
    If recursive=True, it will also include files inside subfolders.
    """
    service = GoogleDriveAuthService.get_service()

    def list_once(fid: str):
        query = f"'{fid}' in parents and trashed=false"
        return service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name, mimeType, size, modifiedTime)",
            pageSize=page_size
        ).execute().get("files", [])

    def list_recursive(fid: str):
        all_files = []
        files = list_once(fid)

        for f in files:
            # Google Drive folder mimeType
            if f.get("mimeType") == "application/vnd.google-apps.folder":
                all_files.extend(list_recursive(f["id"]))
            else:
                all_files.append(f)

        return all_files

    files = list_recursive(folder_id) if recursive else list_once(folder_id)

    print("Files from drive taken by mcp", file=sys.stderr, flush=True)
    print(files, file=sys.stderr, flush=True)

    return {"files": files}



@mcp.tool()
def drive_file_metadata(file_id: str) -> Dict[str, Any]:
    """
    Returns metadata of a Drive file.
    """
    service = GoogleDriveAuthService.get_service()

    meta = service.files().get(
        fileId=file_id,
        fields="id, name, mimeType, size, modifiedTime"
    ).execute()

    return meta


@mcp.tool()
def drive_download_file_bytes(file_id: str) -> Dict[str, Any]:
    """
    Downloads file bytes from Drive and returns file size only (not bytes, for safety).
    If you want bytes, we will use it internally for PDF/video transcript tools.
    """
    service = GoogleDriveAuthService.get_service()
    file_bytes = service.files().get_media(fileId=file_id).execute()

    return {
        "file_id": file_id,
        "size_bytes": len(file_bytes),
        "message": "Downloaded successfully (bytes not returned for safety)."
    }


# --------------------------------------
# PDF TOOL
# --------------------------------------

@mcp.tool()
def pdf_extract_text(file_id: str) -> Dict[str, Any]:
    """
    Extracts readable text from a PDF stored in Google Drive.
    Uses your existing pdf_service: download_and_read_pdf(file_id).
    """
    text = download_and_read_pdf(file_id)

    print("Text from pdf length: " , len(text), file=sys.stderr, flush=True)


    return {
        "file_id": file_id,
        "text": text,
        "length": len(text)
    }


# --------------------------------------
# VIDEO TRANSCRIPT TOOL (Good quality, free)
# --------------------------------------

@mcp.tool()
def video_transcribe_from_drive(file_id: str, model_name: str = "small") -> Dict[str, Any]:
    """
    Downloads video/audio from Google Drive and transcribes using Whisper.
    Default model: small (better quality than tiny, still free).
    """
    service = GoogleDriveAuthService.get_service()

    # Download file bytes
    file_bytes = service.files().get_media(fileId=file_id).execute()

    # Store as temporary file (Whisper works best with a real file)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = whisper.load_model(model_name, device=device)

        result = model.transcribe(tmp_path, fp16=False)
        transcript = result.get("text", "").strip()

        print("Transcript from video: ", transcript, file=sys.stderr, flush=True)

        return {
            "file_id": file_id,
            "model_used": model_name,
            "device": device,
            "transcript": transcript,
            "length": len(transcript)
        }

    finally:
        # Cleanup temp file
        try:
            os.remove(tmp_path)
        except Exception:
            pass


# --------------------------------------
# GEMINI TOOL
# --------------------------------------

@mcp.tool()
def gemini_clean_or_summarize(text: str, mode: str = "clean") -> Dict[str, Any]:
    """
    Uses Gemini to clean or summarize extracted content.
    mode = "clean" | "summary"
    """
    if mode not in ["clean", "summary"]:
        mode = "clean"

    if mode == "clean":
        prompt = f"""
You are an academic content cleaner for an Open Educational Resources platform.

Clean the following text:
- Remove repetition, broken lines, unnecessary fillers
- Keep meaning same
- Keep it simple and structured
- Output clean readable content

TEXT:
{text}
"""
    else:
        prompt = f"""
You are an academic summarizer for an Open Educational Resources platform.

Summarize the following content:
- Keep important definitions
- Keep key points as bullet list
- Keep it short but complete

TEXT:
{text}
"""

    response = llm.invoke(prompt)

    return {
        "mode": mode,
        "output": response.content
    }


# --------------------------------------
# DATABASE TOOLS (Django ORM)
# --------------------------------------

@mcp.tool()
async def db_get_chapter_details(chapter_id: int) -> Dict[str, Any]:
    """
    Fetch syllabus chapter details from DB.
    """
    chapter = await sync_to_async(
        lambda: Chapter.objects.filter(id=chapter_id).select_related("course").first()
    )()
    if not chapter:
        return {"error": f"Chapter {chapter_id} not found"}

    return {
        "chapter_id": chapter.id,
        "chapter_number": chapter.chapter_number,
        "chapter_name": chapter.chapter_name,
        "chapter_description": chapter.description,
        "course_id": chapter.course.id,
        "course_name": chapter.course.course_name,
        "course_code": chapter.course.course_code,
        "scheme": chapter.course.scheme.name if chapter.course.scheme else None,
    }


@mcp.tool()
async def db_create_submission(contributor_id: int, chapter_id: int, drive_folders: Dict[str, str]) -> Dict[str, Any]:
    print("DB_CREATE_SUBMISSION called", file=sys.stderr, flush=True)
    """
    Creates UploadCheck + ContentCheck entry based on which folder contains files.
    This replaces direct DB calls from agent side.
    """

    service = GoogleDriveAuthService.get_service()
    print("GoogleDriveAuthService called", file=sys.stderr, flush=True)
    now = timezone.now()

    # Check which folders have at least 1 file
    content_flags = {"pdf": False, "videos": False}

    for folder_type, folder_id in drive_folders.items():
        if not folder_id:
            continue

        query = f"'{folder_id}' in parents and trashed=false"
        results = service.files().list(
            q=query,
            spaces="drive",
            fields="files(id)",
            pageSize=1
        ).execute()

        if results.get("files"):
            content_flags[folder_type] = True

    chapter_obj = await sync_to_async(Chapter.objects.filter(id=chapter_id).first)()

    if not chapter_obj:
        return {"error": f"Chapter {chapter_id} not found"}

    upload = await sync_to_async(UploadCheck.objects.create)(
        contributor_id=contributor_id,
        chapter=chapter_obj,
        evaluation_status=False,
        timestamp=now
    )

    await sync_to_async(ContentCheck.objects.create)(
        upload=upload,
        pdf=content_flags["pdf"],
        video=content_flags["videos"],
    )

    print("Upload id: ", upload.id, file=sys.stderr, flush=True)
    print("Content flags: ", content_flags, file=sys.stderr, flush=True)

    return {
        "message": "Submission recorded successfully",
        "upload_id": upload.id,
        "content_flags": content_flags
    }


@mcp.tool()
def db_save_scores(upload_id: int, scores: Dict[str, float]) -> Dict[str, Any]:
    """
    Save evaluation scores for a submission.
    Expected keys: completeness, clarity, accuracy, engagement
    """
    upload = UploadCheck.objects.filter(id=upload_id).first()
    if not upload:
        return {"error": f"Upload {upload_id} not found"}

    content_score, _ = ContentScore.objects.get_or_create(upload=upload)

    # Update only provided keys
    if "completeness" in scores:
        content_score.completeness = scores["completeness"]
    if "engagement" in scores:
        content_score.enagagement = scores["engagement"]  # NOTE: your model has typo enagagement
    if "clarity" in scores:
        content_score.clarity = scores["clarity"]
    if "accuracy" in scores:
        content_score.accuracy = scores["accuracy"]

    content_score.save()

    return {"message": "Scores saved successfully", "upload_id": upload_id}


@mcp.tool()
def db_release_content(upload_id: int, release_status: bool = True) -> Dict[str, Any]:
    """
    Mark submission as released.
    """
    upload = UploadCheck.objects.filter(id=upload_id).first()
    if not upload:
        return {"error": f"Upload {upload_id} not found"}

    released_obj, _ = ReleasedContent.objects.get_or_create(upload=upload)
    released_obj.release_status = release_status
    released_obj.save()

    return {"message": "Release status updated", "upload_id": upload_id, "released": release_status}

from typing import Dict, Any
from asgiref.sync import sync_to_async
from accounts.models import UploadCheck, ContentScore


@mcp.tool()
async def db_save_scores_generic(upload_id: int, scores: Dict[str, float]) -> Dict[str, Any]:
    """
    Save evaluation scores for a submission.

    Example payload:
    {
      "clarity": 7.5,
      "coherence": 6.8,
      "completeness": 9.0,
      "relevance": 8.2
    }
    """

    upload = await sync_to_async(lambda: UploadCheck.objects.filter(id=upload_id).first())()
    if not upload:
        return {"error": f"Upload {upload_id} not found"}

    score_obj, _ = await sync_to_async(lambda: ContentScore.objects.get_or_create(upload=upload))()

    # ✅ allowed mapping (supports your typo too)
    allowed_fields = {
        "clarity": "clarity",
        "coherence": "coherence",
        "completeness": "completeness",
        "relevance": "relevance",
        "accuracy": "accuracy",
        "engagement": "engagement",  # your DB typo field
    }

    updated = {}

    for key, value in scores.items():
        key = key.lower().strip()
        if key not in allowed_fields:
            continue

        field_name = allowed_fields[key]

        # Force float + clamp to 0-10
        try:
            value_f = float(value)
        except Exception:
            continue

        value_f = max(0.0, min(10.0, value_f))
        setattr(score_obj, field_name, value_f)
        updated[key] = value_f

    await sync_to_async(score_obj.save)()

    return {
        "message": "Scores saved successfully",
        "upload_id": upload_id,
        "updated_scores": updated
    }

# --------------------------------------
# Start MCP
# --------------------------------------

if __name__ == "__main__":
    try:
        print("MCP MAIN STARTED", file=sys.stderr, flush=True)
        mcp.run()
    except Exception as e:
        print("[MCP] Server crashed:", e, file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        raise

