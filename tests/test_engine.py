import config
import engine
import pytest
import extraction

def test_chunk_document():
    document = extraction.get_document("tests/fixtures/test_doc_long.txt")
    print(engine.chunk_document(document, config.CHUNK_SIZE))
    