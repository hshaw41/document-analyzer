import pymupdf
from docx import Document
from striprtf.striprtf import rtf_to_text

def get_document(filename: str) -> str:
    """Extract and return all text from a document file.

    Supports PDF, DOCX, RTF, plain text and Markdown.
    Raises ValueError for unsupported file types.
    """
    if filename.endswith(".pdf"):
        doc = pymupdf.open(filename)
        document = ""
        for page in doc:
            document += "\n" + page.get_text()
    elif filename.endswith(".docx"):
        doc = Document(filename)
        document = ""
        for paragraph in doc.paragraphs:
            document += "\n" + paragraph.text
    elif filename.endswith(".rtf"):
        with open(filename, "r") as f:
            document = rtf_to_text(f.read())
    elif filename.endswith((".txt", ".md")):
        with open(filename, "r") as f:
            document = f.read()
    else:
        raise ValueError(f"Unsupported file type: {filename}")
    return document