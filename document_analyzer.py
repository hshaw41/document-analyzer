import anthropic
from dotenv import load_dotenv
import json

load_dotenv()

FILENAME = "document.txt"

try:
    with open(FILENAME, "r") as f:
        document = json.load(f)
except FileNotFoundError:
    print(f"Can't find {FILENAME}.")

client = anthropic.Anthropic()

message = client.messages.create(
    model = "claude-haiku-4-5",
    max_tokens = 1000,
    messages = [
        {
            "role": "user",
            "content": "Hi, what are your capabilities?"
        }
    ]
)
print(message.content)
