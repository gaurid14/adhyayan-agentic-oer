import asyncio
import json
import os
import whisper
import mimetypes
from langchain.tools import tool

from langgraph_agents.services.drive_service import GoogleDriveAuthService
from langgraph_agents.services.gemini_service import llm
import datetime
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from accounts.models import UploadCheck, Chapter, ContentCheck
from django.conf import settings


from django.utils import timezone

def record_submission_to_db(contributor_id, chapter_id, drive_folders):
    """
    Called once the contributor confirms submission.
    Creates one UploadCheck + linked ContentCheck.
    """
    service = GoogleDriveAuthService.get_service()
    now = timezone.now()  # Timestamp when Confirm Submission clicked

    # 🔹 Check which content folders actually have files
    content_flags = {"pdf": False, "video": False, "assessment": False}

    for folder_type, folder_id in drive_folders.items():
        if not folder_id:
            continue
        query = f"'{folder_id}' in parents and trashed=false"
        results = service.files().list(
            q=query,
            spaces='drive',
            fields="files(id)",
            pageSize=1
        ).execute()
        if results.get("files"):
            content_flags[folder_type] = True

    # 🔹 Get chapter object
    chapter_obj = Chapter.objects.filter(id=chapter_id).first()
    if not chapter_obj:
        print(f"[WARN] Chapter ID {chapter_id} not found.")
        return None

    # 🔹 Create UploadCheck record (with current time)
    upload = UploadCheck.objects.create(
        contributor_id=contributor_id,
        chapter=chapter_obj,
        evaluation_status=False,
        timestamp=now
    )

    # 🔹 Create ContentCheck record linked to this upload
    ContentCheck.objects.create(
        upload=upload,
        pdf=content_flags["pdf"],
        video=content_flags["video"],
        assessment=content_flags["assessment"]
    )

    print(f"[INFO] UploadCheck + ContentCheck created for contributor {contributor_id} at {now}")
    return upload



# 🔹 Integrate into LangGraph pipeline
@tool
async def submission_agent(contributor_id: int, chapter_id: int, drive_folders: dict, **kwargs) -> dict:
    """
    LangGraph node that finalizes submission after file processing.
    """

    # Run DB insertion in background
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, record_submission_to_db, contributor_id, chapter_id, drive_folders)
    return {"status": "submission_recorded"}





