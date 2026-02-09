import sys

if len(sys.argv) > 1:
    filename = sys.argv[1]
else:
    filename = input("Enter filename: ")

try:
    with open(filename, "r") as f:
        document = f.read()
except FileNotFoundError:
    print(f"Can't find {filename}.")
    exit(1)

print(f"Document length: {len(document)}")