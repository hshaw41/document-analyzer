import extraction
import pytest

def test_get_document_pdf_returns_text():
    filename = "tests/fixtures/test_doc.pdf"
    validation_string = "Attention Is All You Need"
    document = extraction.get_document(filename)
    assert validation_string in document

def test_get_document_docx_returns_text():
    filename = "tests/fixtures/test_doc.docx"
    validation_string = "Career Transition Roadmap"
    document = extraction.get_document(filename)
    assert validation_string in document

def test_get_document_rtf_returns_text():
    filename = "tests/fixtures/test_doc.rtf"
    validation_string = "The Transformer Architecture"
    document = extraction.get_document(filename)
    assert validation_string in document

def test_get_document_txt_returns_text():
    filename = "tests/fixtures/test_doc_short.txt"
    validation_string = "Attention Is All You Need"
    document = extraction.get_document(filename)
    assert validation_string in document

def test_get_document_md_returns_text():
    filename = "tests/fixtures/test_doc.md"
    validation_string = "Attention Is All You Need"
    document = extraction.get_document(filename)
    assert validation_string in document

def test_get_document_unsupported_extension_raises_valueerror():
    filename = "tests/fixtures/test_doc.pages"
    with pytest.raises(ValueError):
        extraction.get_document(filename)

def test_get_document_missing_file_raises():
    filename = "tests/fixtures/test_doc_massive.txt"
    with pytest.raises(FileNotFoundError):
        extraction.get_document(filename)