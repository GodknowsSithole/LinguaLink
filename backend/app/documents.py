"""Reading uploaded documents (.txt, .pdf, .docx) and saving them to disk."""

import io
import os
import re
import uuid

from docx import Document
from fastapi import HTTPException
from pypdf import PdfReader

MAX_CHARS = 8000  # cap the text we translate so CPU-only translation time stays bounded
CHUNK_CHARS = 600  # roughly fits the model's 512-token input limit with some margin
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")

MIME_TYPES = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def save_attachment(filename, data):
    """Save the file under a random name. Returns (path, mime type)."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    extension = os.path.splitext(filename or "")[1].lower()
    stored_name = f"{uuid.uuid4().hex}{extension}"
    path = os.path.join(UPLOAD_DIR, stored_name)

    with open(path, "wb") as f:
        f.write(data)

    return path, MIME_TYPES.get(extension, "application/octet-stream")


def extract_text(filename, data):
    """Return the plain text inside a .txt, .pdf or .docx file."""
    name = (filename or "").lower()

    if name.endswith(".txt"):
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("latin-1")

    if name.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(data))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            raise HTTPException(400, "Could not read PDF file")

    if name.endswith(".docx"):
        try:
            document = Document(io.BytesIO(data))
            return "\n".join(paragraph.text for paragraph in document.paragraphs)
        except Exception:
            raise HTTPException(400, "Could not read DOCX file")

    raise HTTPException(400, "Unsupported document type. Use .txt, .pdf or .docx")


def chunk_text(text, size=CHUNK_CHARS):
    """Split text into pieces of at most `size` characters, breaking at line boundaries."""
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    paragraphs = [line.strip() for line in text.split("\n") if line.strip()]

    chunks = []
    current = ""

    for paragraph in paragraphs:
        candidate = (current + "\n" + paragraph) if current else paragraph

        if len(candidate) > size and current:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate

        # A single very long line is cut into fixed-size pieces.
        while len(current) > size:
            chunks.append(current[:size])
            current = current[size:]

    if current:
        chunks.append(current)

    return chunks or [""]
