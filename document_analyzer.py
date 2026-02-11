import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
import sys
import time
import pymupdf

load_dotenv()

MODEL_PRICING = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-opus-4-5": {"input": 5.00, "output": 25.00}
}

MODEL = "claude-haiku-4-5"
CHUNK_SIZE = 40000
CHARS_PER_TOKEN = 4

PROMPTS = {
    "default": "You are a document summarizer. Provide concise, accurate summaries that capture the main points.",
    "simple": "Summarize this document for someone with no technical background. Avoid jargon and use simple analogies where possible.",
    "structured": "Summarize this document. Start with a one-sentence TLDR, then cover: the problem being solved, the proposed solution, key results, and why it matters.",
    "critical": "Summarize this document, highlighting both its strengths and any limitations or gaps the authors acknowledge."
}

def get_document():
    """This function gets a document to summarise from either user input or command line."""
    
    # Get filename from user
    if len(sys.argv) > 1:
        filename = sys.argv[1]
    else:
        filename = input("Enter filename: ")

    # Parse extension and open accordingly
    if filename.endswith(".pdf"):
        doc = pymupdf.open(filename)
        document = ""
        for page in doc:
            document += "\n" + page.get_text()
    elif filename.endswith((".txt", ".md")):
        with open(filename, "r") as f:
            document = f.read()
    else:
        raise ValueError(f"Unsupported file type: {filename}")
    return document

def chunk_document(document, chunk_size):
    """This function takes a string of text and splits it into clean chunks close to the user's specified size."""

    chunked_document = []
    current_position = 0
    while current_position < len(document): # chunk until the end of document
        split_point = current_position + (chunk_size * CHARS_PER_TOKEN) # calulcate approximate split

        if split_point < len(document): 
            clean_split_point = document.rfind("\n\n", current_position, split_point) 
            if clean_split_point == -1: # if no paragraph break find newline
                clean_split_point = document.rfind("\n", current_position, split_point)
            if clean_split_point == -1: # if no newline find whitespace
                clean_split_point = document.rfind(" ", current_position, split_point)
            if clean_split_point == -1: # if no delimeters found split on the approximate split
                clean_split_point = split_point
            chunk = document[current_position:clean_split_point] # fill chunk
            current_position = clean_split_point + 1
        else: # if chunk past the end, set chunk to the end
            chunk = document[current_position:]
            current_position = len(document)
        chunked_document.append(chunk) # add chunk to list
    return chunked_document

def get_claude_response(client, user_prompt, system_prompt, temperature=1.0, model=MODEL, max_tokens=4096):
    """This function sends a request to claude with a single user prompt and system prompt. Request parameters also can be set and have defaults, these are, temperature, model and maximum tokens."""
    attempts = 3
    for attempt in range(attempts):
        try:
            message = client.messages.create(
                model = model,
                max_tokens = max_tokens,
                messages = [
                    {
                        "role": "user",
                        "content": user_prompt
                    }
                ],
                system = system_prompt,
                temperature = temperature
            )
            return message
        except anthropic.RateLimitError as e:
            last_error = e
            print(f"API Rate limit error. Attempt {attempt+1}/{attempts} failed. Retrying... ")
            time.sleep(60)
    raise last_error

def calculate_response_cost(response, model=MODEL):
    "This function calculates the input and output cost of a claude response API call."

    input_cost = (response.usage.input_tokens / 1000000) * MODEL_PRICING[model]["input"]
    output_cost = (response.usage.output_tokens / 1000000) * MODEL_PRICING[model]["output"]
    return input_cost, output_cost

try:
    document = get_document()
except (FileNotFoundError, pymupdf.FileNotFoundError):
    print(f"File not found.\nexiting...")
    exit(1)
except ValueError as e:
    print(e)
    exit(1)

char_count = len(document)
estimated_tokens = char_count / 4
print(f"Characters: {char_count:,}")
print(f"Estimated tokens: {estimated_tokens:,.0f}")
chunked_document = chunk_document(document, CHUNK_SIZE)
client = anthropic.Anthropic()
prompt_type = "default"

if len(chunked_document) == 1:
    print("Summarizing...")
    system_prompt = PROMPTS[prompt_type]
    temperature = 0.5
    response = get_claude_response(client, document, system_prompt, temperature)
    input_cost, output_cost = calculate_response_cost(response)
    summary = response.content[0].text
else:
    summaries = ""
    input_cost = 0
    output_cost = 0
    map_prompt = PROMPTS[prompt_type]
    temperature = 0.5
    for i, chunk in enumerate(chunked_document):
        print(f"Summarizing chunk {i + 1}/{len(chunked_document)}...")
        response = get_claude_response(client, chunk, map_prompt, temperature)
        summaries += "\n\n" + response.content[0].text
        chunk_input_cost, chunk_output_cost = calculate_response_cost(response)
        input_cost += chunk_input_cost
        output_cost += chunk_output_cost
        time.sleep(60)
    print(f"Generating final summary...")
    reduce_prompt = f"{PROMPTS[prompt_type]} You will receive multiple summaries of sections of a large document. Combine them into one coherent summary."
    response = get_claude_response(client, summaries, reduce_prompt, temperature)
    summary = response.content[0].text
    final_input_cost, final_output_cost = calculate_response_cost(response)
    input_cost += final_input_cost
    output_cost += final_output_cost

console = Console()
console.print(Markdown(summary))
total_cost = input_cost + output_cost
print(f"\nCost Breakdown\n--------------------\nInput: ${input_cost:.6f}\nOutput: ${output_cost:.6f}\nTotal: ${total_cost:.6f}")