import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
import sys
import math

load_dotenv()

MODEL = "claude-haiku-4-5"

MODEL_PRICING = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-opus-4-5": {"input": 5.00, "output": 25.00}
}

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

char_count = len(document)
estimated_tokens = char_count / 4

print(f"Characters: {char_count:,}")
print(f"Estimated tokens: {estimated_tokens:,.0f}")

CHUNK_SIZE = 40000
CHARS_PER_TOKEN = 4

chunked_document = []
chunk_count = math.floor(len(document) / (CHUNK_SIZE * CHARS_PER_TOKEN))

for chunk_index in range(chunk_count + 1):
    #1*160000, 2*160000, ...
    prev_split_point = chunk_index * (CHUNK_SIZE * CHARS_PER_TOKEN)
    split_point = (chunk_index + 1) * (CHUNK_SIZE * CHARS_PER_TOKEN)
    
    if split_point < len(document):
        #print(f"{prev_split_point}:{split_point}")
        chunk = document[prev_split_point:split_point]
    else:
        #print(f"{prev_split_point}:{len(document)}")
        chunk = document[prev_split_point:]
    if len(chunk) > 0:
        chunked_document.append(chunk)
    print(len(chunk))
print(len(chunked_document))

# client = anthropic.Anthropic()

# message = client.messages.create(
#     model = MODEL,
#     max_tokens = 4096,
#     messages = [
#         {
#             "role": "user",
#             "content": document
#         }
#     ],
#     system = "You are a document summarizer. Provide concise, accurate summaries that capture the main points."
# )

# pricing = MODEL_PRICING[MODEL]
# input_cost = (message.usage.input_tokens / 1000000) * pricing["input"]
# output_cost = (message.usage.output_tokens / 1000000) * pricing["output"]
# total_cost = input_cost + output_cost
# summary = message.content[0].text

# console = Console()
# console.print(Markdown(summary))
# print(f"\nCost Breakdown\n--------------------\nInput: ${input_cost:.6f}\nOutput: ${output_cost:.6f}\nTotal: ${total_cost:.6f}")
