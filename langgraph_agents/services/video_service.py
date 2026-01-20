import io
from http import client

import openai
import whisper
from googleapiclient.http import MediaIoBaseDownload



# def transcribe_audio_or_video(state: dict) -> dict:
#     """
#     Transcribes audio/video files using free local Whisper model.
#     """
#     file_path = state["file_path"]
#
#     # Load Whisper model (base is a good balance of speed & accuracy) but the web request is timing out so using a smaller,
#     # faster model like "tiny" or "small". This will significantly reduce the transcription time and should allow the process
#     # to finish before any timeouts occur.
#     model = whisper.load_model("tiny")
#
#     try:
#         result = model.transcribe(file_path)
#         state["transcript"] = result["text"]
#     except Exception as e:
#         state["transcript"] = f"Transcription failed: {e}"
#
#     return state

import whisper
import torch
import os

def transcribe_audio_or_video(state: dict) -> dict:
    """
    Transcribes local audio/video using Whisper.
    Stores transcript in state['transcript'].
    """
    file_path = state.get("file_path")
    if not file_path or not os.path.exists(file_path):
        state["transcript"] = "[Error] File path invalid or missing."
        return state

    try:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = whisper.load_model("tiny", device=device)

        print(f"[INFO] Transcribing: {file_path} using {device.upper()}")
        result = model.transcribe(file_path, fp16=False)
        transcript = result.get("text", "").strip()

        state["transcript"] = transcript
        print(f"[INFO] Transcription complete — {len(transcript)} characters")
    except Exception as e:
        print(f"[ERROR] Whisper transcription failed: {e}")
        state["transcript"] = f"[ERROR] {e}"

    return state


