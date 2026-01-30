from typing import Dict, Any, List
import json
import re
import sys
import os
import threading
import asyncio
from datetime import datetime

from langchain.tools import tool
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Where extracted json will be stored
EXTRACTED_JSON_DIR = os.path.join(BASE_DIR, "storage", "extracted_content")
MCP_PATH = os.path.join(BASE_DIR, "langgraph_agents", "services", "mcp_server.py")


# ----------------------------
# Helpers
# ----------------------------
def ensure_dir_exists(path: str):
    os.makedirs(path, exist_ok=True)


def save_extracted_json(upload_id: int, data: dict) -> str:
    """
    Saves extracted content JSON to disk.
    Returns file path.
    """
    ensure_dir_exists(EXTRACTED_JSON_DIR)
    json_path = os.path.join(EXTRACTED_JSON_DIR, f"upload_{upload_id}.json")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return json_path


def filter_files(files: List[dict], allowed_mimetypes: List[str]) -> List[dict]:
    return [f for f in files if f.get("mimeType") in allowed_mimetypes]


def word_count(text: str) -> int:
    if not text:
        return 0
    return len(re.findall(r"\b\w+\b", text))


def chunk_text(text: str, max_chars: int = 4500) -> List[str]:
    if not text:
        return []
    chunks = []
    start = 0
    n = len(text)

    while start < n:
        end = min(start + max_chars, n)
        slice_text = text[start:end]
        cut = slice_text.rfind("\n\n")
        if cut != -1 and end != n and cut > 500:
            end = start + cut
        chunks.append(text[start:end].strip())
        start = end

    return [c for c in chunks if c]


def detect_language_heuristic(text: str) -> str:
    if not text or len(text.strip()) < 50:
        return "unknown"
    total = len(text)
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    ratio = non_ascii / max(total, 1)
    return "non_english_or_mixed" if ratio > 0.15 else "english"


# ----------------------------
# BACKGROUND EXTRACTION LOGIC (JSON SAVING ADDED)
# ----------------------------
async def run_extraction_background(contributor_id: int, chapter_id: int, upload_id: int, drive_folders: dict):
    """
    Runs after DB record created.
    Extracts pdf text + video transcripts.
    Saves extracted content into JSON file.
    """

    print("\n🚀 Background Extraction Started for upload_id:", upload_id)

    pdf_folder_id = drive_folders.get("pdf")
    video_folder_id = drive_folders.get("videos")

    MCP_PATH = os.path.join(BASE_DIR, "langgraph_agents", "services", "mcp_server.py")

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[MCP_PATH],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ----------------------------
            # 1) Chapter details
            # ----------------------------
            chapter_resp = await session.call_tool(
                "db_get_chapter_details",
                {"chapter_id": chapter_id},
            )
            try:
                chapter_data = json.loads(chapter_resp.content[0].text)
            except Exception:
                chapter_data = {}

            # ----------------------------
            # 2) PDF Extraction
            # ----------------------------
            pdf_texts = []
            if pdf_folder_id:
                pdf_files_resp = await session.call_tool(
                    "drive_list_files",
                    {"folder_id": pdf_folder_id, "page_size": 200, "recursive": True},
                )
                try:
                    pdf_files_data = json.loads(pdf_files_resp.content[0].text)
                except Exception:
                    pdf_files_data = {}

                pdf_files = filter_files(pdf_files_data.get("files", []), ["application/pdf"])

                for f in pdf_files:
                    extract_resp = await session.call_tool("pdf_extract_text", {"file_id": f["id"]})
                    extracted_text = ""
                    try:
                        extract_json = json.loads(extract_resp.content[0].text)
                        extracted_text = extract_json.get("text", "") or ""
                    except Exception:
                        extracted_text = ""

                    pdf_texts.append({
                        "file_id": f["id"],
                        "file_name": f.get("name", "Unknown PDF"),
                        "text": extracted_text,
                    })

            # ----------------------------
            # 3) Video Transcript Extraction
            # ----------------------------
            video_transcripts = []
            if video_folder_id:
                video_files_resp = await session.call_tool(
                    "drive_list_files",
                    {"folder_id": video_folder_id, "page_size": 200, "recursive": True},
                )
                try:
                    video_files_data = json.loads(video_files_resp.content[0].text)
                except Exception:
                    video_files_data = {}

                video_files = filter_files(
                    video_files_data.get("files", []),
                    [
                        "video/mp4",
                        "video/mpeg",
                        "video/quicktime",
                        "audio/mpeg",
                        "audio/mp3",
                        "audio/wav",
                        "audio/x-wav",
                    ],
                )

                for f in video_files:
                    transcript_resp = await session.call_tool(
                        "video_transcribe_from_drive",
                        {"file_id": f["id"], "model_name": "small"},
                    )
                    transcript = ""
                    try:
                        transcript_json = json.loads(transcript_resp.content[0].text)
                        transcript = transcript_json.get("transcript", "") or ""
                    except Exception:
                        transcript = ""

                    video_transcripts.append({
                        "file_id": f["id"],
                        "file_name": f.get("name", "Unknown Video"),
                        "transcript": transcript,
                    })

            # ----------------------------
            # 4) Combine & Stats
            # ----------------------------
            combined_parts = []

            if chapter_data.get("chapter_name"):
                combined_parts.append(
                    f"CHAPTER CONTEXT:\n{chapter_data.get('chapter_name')}\n\n{chapter_data.get('chapter_description', '')}"
                )

            for p in pdf_texts:
                if p["text"].strip():
                    combined_parts.append(f"PDF FILE: {p['file_name']}\n\n{p['text']}")

            for v in video_transcripts:
                if v["transcript"].strip():
                    combined_parts.append(f"VIDEO FILE: {v['file_name']}\n\n{v['transcript']}")

            combined_text = "\n\n---\n\n".join(combined_parts).strip()

            combined_words = word_count(combined_text)
            language = detect_language_heuristic(combined_text)
            chunks = chunk_text(combined_text)

            # FINAL JSON PAYLOAD TO STORE
            extracted_payload = {
                "upload_id": upload_id,
                "contributor_id": contributor_id,
                "chapter_id": chapter_id,
                "created_at": datetime.utcnow().isoformat(),
                "drive_folders": drive_folders,
                "chapter_details": chapter_data,
                "content": {
                    "pdfs": pdf_texts,
                    "videos": video_transcripts,
                    "combined_text": combined_text,
                    "chunks": chunks,
                },
                "stats": {
                    "pdf_count": len(pdf_texts),
                    "video_count": len(video_transcripts),
                    "combined_word_count": combined_words,
                    "language": language,
                }
            }

            # SAVE JSON TO FILE
            json_path = save_extracted_json(upload_id, extracted_payload)

            from asgiref.sync import sync_to_async
            from django.utils import timezone
            from accounts.models import ContentCheck

            await sync_to_async(
                lambda: ContentCheck.objects.filter(upload_id=upload_id).update(
                    extraction_status=True,
                    extraction_updated_at=timezone.now()
                )
            )()


            print("\nBACKGROUND EXTRACTION DONE")
            print("Upload ID:", upload_id)
            print("PDFs:", len(pdf_texts))
            print("Videos:", len(video_transcripts))
            print("Words:", combined_words)
            print("Language:", language)
            print("📄 Saved JSON at:", json_path)


def start_background_extraction(contributor_id: int, chapter_id: int, upload_id: int, drive_folders: dict):
    """
    Thread wrapper so it doesn't block user request.
    Creates its own asyncio loop.
    """
    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                run_extraction_background(contributor_id, chapter_id, upload_id, drive_folders)
            )
        finally:
            loop.close()

    threading.Thread(target=runner, daemon=True).start()


# ----------------------------
# MAIN SUBMISSION AGENT
# ----------------------------
async def smart_submission_agent(contributor_id: int, chapter_id: int, drive_folders: dict) -> Dict[str, Any]:
    print("Smart submission agent")

    if not contributor_id:
        return {"status": "failed", "reason": "Missing contributor_id"}
    if not chapter_id:
        return {"status": "failed", "reason": "Missing chapter_id"}
    if not isinstance(drive_folders, dict):
        return {"status": "failed", "reason": "drive_folders must be dict"}

    pdf_folder_id = drive_folders.get("pdf")
    video_folder_id = drive_folders.get("videos")

    if not pdf_folder_id and not video_folder_id:
        return {"status": "failed", "reason": "No content folders found"}

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[MCP_PATH],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1) Create DB record FAST
            submission_resp = await session.call_tool(
                "db_create_submission",
                {"contributor_id": contributor_id, "chapter_id": chapter_id, "drive_folders": drive_folders},
            )

            try:
                submission_data = json.loads(submission_resp.content[0].text)
            except Exception:
                submission_data = {}

            if "error" in submission_data:
                return {"status": "failed", "reason": submission_data["error"]}

            upload_id = submission_data.get("upload_id")

            # 2) Start extraction in background (non-blocking)
            start_background_extraction(contributor_id, chapter_id, upload_id, drive_folders)

            # 3) Return IMMEDIATELY to user
            return {
                "status": "success",
                "message": "Submission recorded successfully. Extraction running in background.",
                "upload_id": upload_id,
                "json_path": os.path.join("storage", "extracted_content", f"upload_{upload_id}.json"),
                "next_action": "SHOW_SUCCESS_PAGE",
            }


@tool
async def submission_agent(contributor_id: int, chapter_id: int, drive_folders: dict) -> Dict[str, Any]:
    """Creates submission DB record quickly and runs extraction in background thread. Saves extracted content JSON."""
    return await smart_submission_agent(contributor_id, chapter_id, drive_folders)
