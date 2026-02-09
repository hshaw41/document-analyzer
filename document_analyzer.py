import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
import sys
import time

load_dotenv()

MODEL = "claude-haiku-4-5"

MODEL_PRICING = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-opus-4-5": {"input": 5.00, "output": 25.00}
}

CHUNK_SIZE = 40000
CHARS_PER_TOKEN = 4

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

pricing = MODEL_PRICING[MODEL]

chunked_document = []
current_position = 0

while current_position < len(document):
    split_point = current_position + (CHUNK_SIZE * CHARS_PER_TOKEN)

    if split_point < len(document):
        clean_split_point = document.rfind("\n\n", current_position, split_point)
        if clean_split_point == -1:
            clean_split_point = document.rfind("\n", current_position, split_point)
        if clean_split_point == -1:
            clean_split_point = document.rfind(" ", current_position, split_point)
        if clean_split_point == -1:
            clean_split_point = split_point
        chunk = document[current_position:clean_split_point]
        current_position = clean_split_point + 1
    else:
        chunk = document[current_position:]
        current_position = len(document)
    chunked_document.append(chunk)

client = anthropic.Anthropic()

input_cost = 0
output_cost = 0

if len(chunked_document) == 1:
    print("Summarizing...")
    message = client.messages.create(
        model = MODEL,
        max_tokens = 4096,
        messages = [
            {
                "role": "user",
                "content": chunked_document[0]
            }
        ],
        system = "You are a document summarizer. Provide concise, accurate summaries that capture the main points."
    )
    input_cost += (message.usage.input_tokens / 1000000) * pricing["input"]
    output_cost += (message.usage.output_tokens / 1000000) * pricing["output"]
    final_summary = message.content[0].text
else:
    summaries = ""
    for i, chunk in enumerate(chunked_document):
        print(f"Summarizing chunk {i + 1}/{len(chunked_document)}...")
        message = client.messages.create(
            model = MODEL,
            max_tokens = 4096,
            messages = [
                {
                    "role": "user",
                    "content": chunk
                }
            ],
            system = "You are a document summarizer. Provide concise, accurate summaries that capture the main points."
        )
        summary = message.content[0].text
        summaries += "\n\n" + summary 
        input_cost += (message.usage.input_tokens / 1000000) * pricing["input"]
        output_cost += (message.usage.output_tokens / 1000000) * pricing["output"]
        time.sleep(60)
    print(f"Generating final summary...")
    message = client.messages.create(
        model = MODEL,
        max_tokens = 4096,
        messages = [
            {
                "role": "user",
                "content": summaries
            }
        ],
        system = "You are a master document summarizer. You will receive one or multiple summaries for a large document. Combine all summaries into one coherent summary."
    )
    input_cost += (message.usage.input_tokens / 1000000) * pricing["input"]
    output_cost += (message.usage.output_tokens / 1000000) * pricing["output"]
    final_summary = message.content[0].text

console = Console()
console.print(Markdown(final_summary))

total_cost = input_cost + output_cost
print(f"\nCost Breakdown\n--------------------\nInput: ${input_cost:.6f}\nOutput: ${output_cost:.6f}\nTotal: ${total_cost:.6f}")