"""Текст из документов: PDF, DOCX, TXT/MD/CSV и исходники."""

import io

from docx import Document as Docx
from pypdf import PdfReader

MAX_FILE = 20 * 1024 * 1024  # бот может скачать из Telegram файлы до 20 МБ
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".xml", ".yaml", ".yml", ".log", ".sql"}


class FileError(ValueError):
    pass


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise FileError("PDF защищён паролем")
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages[:300])
    elif name.endswith(".docx"):
        doc = Docx(io.BytesIO(data))
        text = "\n".join(p.text for p in doc.paragraphs)
        for table in doc.tables:  # таблицы тоже текст: строка — ячейки через «|»
            text += "\n" + "\n".join(" | ".join(c.text for c in row.cells) for row in table.rows)
    elif any(name.endswith(ext) for ext in TEXT_EXT):
        text = data.decode("utf-8", errors="replace")
    else:
        raise FileError("Поддерживаю PDF, DOCX и текстовые файлы")
    text = text.strip()
    if not text:
        raise FileError("В файле нет текста (возможно, это скан — пришлите его как фото)")
    return text
