import pymupdf
import sys

doc = pymupdf.open(sys.argv[1])
text = "\n".join(page.get_text() for page in doc)

with open(sys.argv[1].replace(".pdf", ".txt"), "w") as f:
    f.write(text)