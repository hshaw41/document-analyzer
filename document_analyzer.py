import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from striprtf.striprtf import rtf_to_text
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
    "simple": "You are a science communicator who explains complex research to general audiences. Summarize this document using no jargon, simple analogies, and plain language. The goal is for the reader to understand what the document covers and why it matters in less than five minutes. Base your summary strictly on the content of the provided document. If something is unclear or not covered in the document, say so rather than speculating.",
    "in_depth": "You are a technical writer who explains research clearly without sacrificing accuracy. Summarize this document with full technical detail, explaining why each concept, method, and result matters. The goal is for the reader to fully understand the paper's contributions, methods, and results. Base your summary strictly on the content of the provided document. If something is unclear or not covered in the document, say so rather than speculating.",
    "expert": "You are a research scientist summarizing a paper for a knowledgeable peer. Provide a research-grade summary including limitations, implementation details, comparisons to related work, and mathematical or architectural specifics. The goal is to give the reader a deep enough understanding to consider implementing or reproducing ideas from the paper. Base your summary strictly on the content of the provided document. If something is unclear or not covered in the document, say so rather than speculating."
}

API_ERRORS = (anthropic.APIConnectionError, anthropic.APIError, anthropic.APITimeoutError, anthropic.RateLimitError)

def get_document(filename):
    """This function extracts all text from a document with a given filename and returns it. PDF, DOCX, RTF, Text and Markdown supported."""

    # Parse extension and open accordingly
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
        # handle rich text documents.
        with open(filename, "r") as f:
            document = rtf_to_text(f.read())
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


# Main

client = anthropic.Anthropic() # connect to anthropic API
console = Console() # instantiate console formatting tools

cli_filename = None
if len(sys.argv) > 1:
    cli_filename = sys.argv[1]

while True:
    # Main menu
    print("Document Analyzer")
    print("-----------------")
    print("1. Summarise a document")
    print("2. Open past summary")
    print("3. Quit")
    choice = input("Enter the number that matches your chosen option: ")
    if choice == "1": # Summarise

        # Get filename from user
        if cli_filename:
            filename = cli_filename
            cli_filename = None # consume the filename given on the command line.
        else:
            filename = input("Enter filename: ")

        try:
            document = get_document(filename)
        except (FileNotFoundError, pymupdf.FileNotFoundError, PackageNotFoundError):
            print(f"File not found.")
            continue
        except ValueError as e:
            print(e)
            continue

        char_count = len(document)
        print(f"Characters: {char_count:,}")
        estimated_tokens = char_count / 4
        print(f"Estimated tokens: {estimated_tokens:,.0f}")

        chunked_document = chunk_document(document, CHUNK_SIZE) # chunk the document if it's large.

        print("Select prompt type from:")
        prompt_keys = list(PROMPTS.keys())
        for i, prompt_type in enumerate(prompt_keys):
            print(f"{i+1}. {prompt_type}")
        while True:
            prompt_type_choice = input("Choice: ")
            if prompt_type_choice.isdigit() and 1 <= int(prompt_type_choice) <= len(prompt_keys):
                prompt_type = prompt_keys[int(prompt_type_choice) - 1]
                break
            print("Invalid choice, try again.")

        summaries = ""
        input_cost = 0
        output_cost = 0

        if len(chunked_document) == 1: # if single chunk
            print("Summarizing...")
            system_prompt = PROMPTS[prompt_type]
            temperature = 0.5
            try:
                response = get_claude_response(client, document, system_prompt, temperature)
                input_cost, output_cost = calculate_response_cost(response)
                summary = response.content[0].text
            except API_ERRORS as e:
                print(f"Failed to summarize: {e}")
                print("There has been no API cost for this summary.")
                continue

        else:
            map_prompt = PROMPTS[prompt_type]
            temperature = 0.5
            try:
                for i, chunk in enumerate(chunked_document):
                    print(f"Summarizing chunk {i + 1}/{len(chunked_document)}...")
                    response = get_claude_response(client, chunk, map_prompt, temperature)
                    summaries += "\n\n" + response.content[0].text
                    chunk_input_cost, chunk_output_cost = calculate_response_cost(response)
                    input_cost += chunk_input_cost
                    output_cost += chunk_output_cost
                    time.sleep(60)
            except API_ERRORS as e:
                print(f"Failed on chunk {i + 1}/{len(chunked_document)}")
                if not summaries: # No summaries were generated yet?
                    print("No chunks summarised.")
                    print("There has been no API cost for this summary.")
                    continue
                else:
                    print(f"Attempting partial summary from {i} completed chunks.")
            print(f"Generating final summary...")
            reduce_prompt = f"{PROMPTS[prompt_type]} You will receive multiple summaries of sections of a large document. Combine them into one coherent summary."
            try:
                response = get_claude_response(client, summaries, reduce_prompt, temperature)
                summary = response.content[0].text
                final_input_cost, final_output_cost = calculate_response_cost(response)
                input_cost += final_input_cost
                output_cost += final_output_cost
            except API_ERRORS as e:
                print("Failed to combine summaries.")
                print("Displaying successful chunk summaries")
                console.print(Markdown(summaries))
                total_cost = input_cost + output_cost
                print(f"\nCost Breakdown\n--------------------\nInput: ${input_cost:.6f}\nOutput: ${output_cost:.6f}\nTotal: ${total_cost:.6f}")
                continue

        console.print(Markdown(summary))
        total_cost = input_cost + output_cost
        print(f"\nCost Breakdown\n--------------------\nInput: ${input_cost:.6f}\nOutput: ${output_cost:.6f}\nTotal: ${total_cost:.6f}")
    elif choice == "2": # Browse
        print("not here yet")
    elif choice == "3": # Quit
        print("Exiting...")
        exit(0)
    else: # Invalid Input
        print("Invalid option, please enter an option in the below list")