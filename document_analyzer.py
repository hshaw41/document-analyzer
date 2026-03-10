import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from striprtf.striprtf import rtf_to_text
from datetime import datetime, timezone
import time
import pymupdf
import os
import json
import argparse

load_dotenv()

MODEL_PRICING = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-opus-4-5": {"input": 5.00, "output": 25.00}
}

MODEL = "claude-haiku-4-5"
CHUNK_SIZE = 40000
CHARS_PER_TOKEN = 4
THINKING_BUDGET = 10000

SUMMARY_PROMPTS = {
    "simple": "You are a science communicator who explains complex research to general audiences. summarise this document using no jargon, simple analogies, and plain language. The goal is for the reader to understand what the document covers and why it matters in less than five minutes. Base your summary strictly on the content of the provided document. If something is unclear or not covered in the document, say so rather than speculating.",
    "in_depth": "You are a technical writer who explains research clearly without sacrificing accuracy. summarise this document with full technical detail, explaining why each concept, method, and result matters. The goal is for the reader to fully understand the paper's contributions, methods, and results. Base your summary strictly on the content of the provided document. If something is unclear or not covered in the document, say so rather than speculating.",
    "expert": "You are a research scientist summarizing a paper for a knowledgeable peer. Provide a research-grade summary including limitations, implementation details, comparisons to related work, and mathematical or architectural specifics. The goal is to give the reader a deep enough understanding to consider implementing or reproducing ideas from the paper. Base your summary strictly on the content of the provided document. If something is unclear or not covered in the document, say so rather than speculating."
}

QANDA_PROMPTS = {
    "simple": "You are a science communicator who explains complex research to general audiences. From a document summary answer questions about the summary using no jargon, simple analogies, and plain language. The goal of this discussion is to gain an understanding of what the document covers and why it matters in less than a 10 minute conversation. Base your answers strictly on the content of the summary. If something is unclear or not covered by the summary, say so rather than speculating.",
    "in_depth": "You are a technical writer who explains research clearly without sacrificing accuracy. From a document summary answer questions about that summary with full technical detail, explaining why each concept, method and result matters. The goal of this conversation is for the user to fully understand the paper's contributions, methods and results. Base your answers strictly on the content of the provided summary of a research paper. If something is unclear or not covered by the summary, say so rather than speculating.",
    "expert": "You are a research scientist discussing a paper with a knowledgeable peer. Provide research-grade answers to the user's questions including limitations, implementation details, comparisons to related work, and mathematical or architectural specifics. The goal of this conversation is to give the user a deep enough understanding to consider implementing or reproducing ideas from the paper. Base your answers strictly on the content of the provided summary of a research paper. If something is unclear or not covered by the summary, say so rather than speculating."
}

REDUCE_INSTRUCTIONS = " You will receive multiple summaries of sections of a large document. Combine them into one coherent summary."
SUMMARY_STRUCTURED_OUTPUT_INSTRUCTIONS = "In the tldr field place a one sentence overview of the document. In the key_terms field, list the technical terms and concepts of the document. Place the full summary in the summary field."

API_ERRORS = (anthropic.APIConnectionError, anthropic.APIError, anthropic.APITimeoutError, anthropic.RateLimitError)

SUMMARY_OUTPUT_CONFIG = {
    "format": {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                "tldr": {"type": "string"},
                "key_terms": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"}
            },
            "required": ["tldr", "key_terms", "summary"],
            "additionalProperties": False
        }
    }
}

# Utilities

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
        split_point = current_position + (chunk_size * CHARS_PER_TOKEN) # calculate approximate split

        if split_point < len(document): 
            clean_split_point = document.rfind("\n\n", current_position, split_point) 
            if clean_split_point == -1: # if no paragraph break find newline
                clean_split_point = document.rfind("\n", current_position, split_point)
            if clean_split_point == -1: # if no newline find whitespace
                clean_split_point = document.rfind(" ", current_position, split_point)
            if clean_split_point == -1: # if no delimiters found split on the approximate split
                clean_split_point = split_point
            chunk = document[current_position:clean_split_point] # fill chunk
            current_position = clean_split_point + 1
        else: # if chunk past the end, set chunk to the end
            chunk = document[current_position:]
            current_position = len(document)
        chunked_document.append(chunk) # add chunk to list
    return chunked_document

# Display

def display_summary_info(tldr, key_terms):
    """This function displays document summary information. It takes individual info items and displays them if the info is available."""

    if tldr:
        print(f"\nTLDR: {tldr}")
    if key_terms:
        print(f"\nKey Terms: {', '.join(key_terms)}")
    return

def display_debug_info(model, char_count, estimated_tokens, input_tokens, output_tokens, chunks, input_cost, output_cost):
    """This summary displays debug information for developers. It displays the Model, document characters, document estimated tokens, actual input and output tokens sent to the model, chunks if applicable and the cost breakdown."""

    print("\n[DEBUG]")
    print(f"Model: {model}")
    print(f"Characters: {char_count}")
    print(f"Estimated Tokens: {estimated_tokens}")
    if input_tokens and output_tokens:
        print(f"Actual Tokens: {input_tokens} in / {output_tokens} out")
    if chunks:
        print(f"Chunks: {chunks}")
    if input_cost and output_cost:
        print(f"Cost: ${input_cost:.6f} in / ${output_cost:.6f} out / {(input_cost+output_cost):.6f} total")
    return

# API

def get_claude_response(client, messages, system_prompt, temperature=1.0, model=MODEL, max_tokens=4096, output_config=None, thinking_budget=None):
    """This function sends a request to claude with a list of messages and system prompt. Request parameters also can be set and have defaults, these are, temperature, model and maximum tokens. Optionally output_config can also be provided, otherwise it will not be used."""
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "system": system_prompt,
        "temperature": temperature,
    }
    if output_config:
        kwargs["output_config"] = output_config
    if thinking_budget:
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
        kwargs["max_tokens"] = max_tokens + thinking_budget # adding thinking budget to the total token budget
        if temperature != 1.0:
            print("Warning: can't have temperature below 1.0 when thinking is enabled. Setting back to 1.0")
            kwargs["temperature"] = 1.0 # forcing temp to be one because thinking only works when this is the case
    attempts = 3
    for attempt in range(attempts):
        try:
            message = client.messages.with_raw_response.create(**kwargs)
            return message
        except anthropic.RateLimitError as e:
            last_error = e
            print(f"API Rate limit error. Attempt {attempt+1}/{attempts} failed. Retrying... ")
            retry_after = e.response.headers.get("retry-after")
            if retry_after:
                time.sleep(float(retry_after))
            else:
                time.sleep(60)
    raise last_error

def calculate_response_cost(response, model=MODEL):
    """This function calculates the input and output cost of a claude response API call."""

    input_cost = (response.usage.input_tokens / 1000000) * MODEL_PRICING[model]["input"]
    output_cost = (response.usage.output_tokens / 1000000) * MODEL_PRICING[model]["output"]
    return input_cost, output_cost

# Core Logic

def summarise_document(client, document, prompt_type, extended_thinking, saved_chunk_summaries=None):
    
    """This function takes a document and summarises it with a chosen prompt type. It returns the summary, it's tldr, key-terms, input and output costs. It returns None on failure."""

    # Chunk document
    chunked_document = chunk_document(document, CHUNK_SIZE)
    chunks = len(chunked_document)

    chunk_summaries = [] # tracks individual chunk summaries in a list

    if saved_chunk_summaries:
        chunk_summaries = saved_chunk_summaries
        summaries = "\n\n".join(saved_chunk_summaries)
        start_index = len(saved_chunk_summaries)
    else:
        summaries = ""
        start_index = 0

    input_cost = 0
    output_cost = 0
    input_tokens = 0
    output_tokens = 0
    if extended_thinking == True:
        thinking_budget = THINKING_BUDGET
    else:
        thinking_budget = None

    if chunks == 1: # if single chunk
        system_prompt = SUMMARY_PROMPTS[prompt_type] + SUMMARY_STRUCTURED_OUTPUT_INSTRUCTIONS
        messages = [
            {
                "role": "user",
                "content": document
            }
        ]
        try:
            progress = Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                TimeElapsedColumn(),
            )
            with progress:
                task = progress.add_task("Summarising...", total=None)
                response = get_claude_response(client, messages, system_prompt, output_config=SUMMARY_OUTPUT_CONFIG, thinking_budget=thinking_budget)
            message = response.parse()
            message_text = next(message_block.text for message_block in message.content if message_block.type == "text")
            input_cost, output_cost = calculate_response_cost(message)
            input_tokens = message.usage.input_tokens
            output_tokens = message.usage.output_tokens
            result = json.loads(message_text)
            summary = result["summary"]
            tldr = result["tldr"]
            key_terms = result["key_terms"]
        except API_ERRORS as e:
            print(f"Failed to summarise: {e}")
            print("There has been no API cost for this summary.")
            return 

    else:
        map_prompt = SUMMARY_PROMPTS[prompt_type]
        tokens_remaining = 50000
        reset_time_utc = datetime.now(timezone.utc)
        print()
        try:
            progress = Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                BarColumn(),
                TimeElapsedColumn(),
            )
            with progress:
                if saved_chunk_summaries:
                    task = progress.add_task(f"Resuming from chunk {start_index + 1}...", total=chunks)
                    progress.update(task, completed=start_index)
                else:
                    task = progress.add_task("Starting...", total = chunks)
                for i, chunk in enumerate(chunked_document):
                    if i < start_index:
                        continue
                    progress.update(task, description=f"Summarising Chunk {i + 1}/{chunks}")
                    messages = [
                        {
                            "role": "user",
                            "content": chunk
                        }
                    ]
                    estimated_next_tokens = (len(chunk) + len(map_prompt)) / 4
                    if tokens_remaining < estimated_next_tokens:
                        current_time_utc = datetime.now(timezone.utc)
                        time_delta = reset_time_utc - current_time_utc
                        seconds_to_sleep = time_delta.total_seconds()
                        if seconds_to_sleep > 0:
                            time.sleep(seconds_to_sleep)
                    response = get_claude_response(client, messages, map_prompt, thinking_budget=thinking_budget)
                    progress.advance(task)
                    message = response.parse()
                    message_text = next(message_block.text for message_block in message.content if message_block.type == "text")
                    chunk_summaries.append(message_text)
                    summaries += "\n\n" + message_text
                    chunk_input_cost, chunk_output_cost = calculate_response_cost(message)
                    input_cost += chunk_input_cost
                    output_cost += chunk_output_cost
                    input_tokens += message.usage.input_tokens
                    output_tokens += message.usage.output_tokens
                    tokens_remaining = int(response.headers.get("anthropic-ratelimit-input-tokens-remaining"))
                    reset_time_string = response.headers.get("anthropic-ratelimit-input-tokens-reset")
                    reset_time_utc = datetime.fromisoformat(reset_time_string)
        except API_ERRORS as e:
            print(f"Failed on chunk {i + 1}/{chunks}")
            if not chunk_summaries: # No summaries were generated yet?
                print("No chunks summarised.")
                print("There has been no API cost for this summary.")
                return
            else:
                print(f"Attempting partial summary from {i} completed chunks.")
        reduce_prompt = SUMMARY_PROMPTS[prompt_type] + SUMMARY_STRUCTURED_OUTPUT_INSTRUCTIONS + REDUCE_INSTRUCTIONS
        messages = [
            {
                "role": "user",
                "content": summaries
            }
        ]
        try:
            estimated_next_tokens = (len(summaries) + len(reduce_prompt)) / 4
            progress = Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                TimeElapsedColumn(),
            )
            with progress:
                task = progress.add_task("Generating final summary...", total=None)
                if tokens_remaining < estimated_next_tokens:
                    current_time_utc = datetime.now(timezone.utc)
                    time_delta = reset_time_utc - current_time_utc
                    seconds_to_sleep = time_delta.total_seconds()
                    if seconds_to_sleep > 0:
                        time.sleep(seconds_to_sleep)
                response = get_claude_response(client, messages, reduce_prompt, output_config=SUMMARY_OUTPUT_CONFIG, thinking_budget=thinking_budget)
            message = response.parse()
            message_text = next(message_block.text for message_block in message.content if message_block.type == "text")
            final_input_cost, final_output_cost = calculate_response_cost(message)
            input_cost += final_input_cost
            output_cost += final_output_cost
            input_tokens += message.usage.input_tokens
            output_tokens += message.usage.output_tokens
            result = json.loads(message_text)
            summary = result["summary"]
            tldr = result["tldr"]
            key_terms = result["key_terms"]
        except API_ERRORS as e:
            print("Failed to combine summaries.")
            print("Displaying successful chunk summaries")
            tldr=None
            key_terms=None
            return summaries, tldr, key_terms, input_tokens, output_tokens, chunks, input_cost, output_cost, chunk_summaries
    return summary, tldr, key_terms, input_tokens, output_tokens, chunks, input_cost, output_cost, None

def get_or_generate_summary(client, filename, document, prompt_type, extended_thinking):
    """This function gets a summary and returns it, either by retrieving it from a saved summary or generating a new one."""

    char_count = len(document)
    estimated_tokens = char_count / 4
    saved_summary = load_summary(filename)
    if saved_summary and "summaries" in saved_summary and prompt_type in saved_summary["summaries"]:
        print("\nCached summary loaded.")
        summary = saved_summary["summaries"][prompt_type]
        tldr = saved_summary.get("tldr")
        key_terms = saved_summary.get("key_terms")
        display_summary_info(tldr, key_terms)
    else:
        saved_chunk_summaries = None
        if saved_summary and "partial_summaries" in saved_summary and saved_summary.get("partial_prompt_type") == prompt_type:
            saved_chunk_summaries = saved_summary["partial_summaries"]
            print(f"\nResuming summarisation from chunk {len(saved_chunk_summaries)}.")

        # Generate Summary
        summary_result = summarise_document(client, document, prompt_type, extended_thinking, saved_chunk_summaries)
        if not summary_result:
            return
        # Parse result
        summary, tldr, key_terms, input_tokens, output_tokens, chunks, input_cost, output_cost, chunk_summaries = summary_result

        # Display
        display_summary_info(tldr, key_terms)
        if DEBUG:
            display_debug_info(MODEL, char_count, estimated_tokens, input_tokens, output_tokens, chunks, input_cost, output_cost)
        
        # Persistence
        if chunk_summaries: # partial failure, add partial summary to save file so summary can be resumed
            save_partial_summaries(filename, chunk_summaries, prompt_type)
        else: # successful summary, clear any partial summaries and save summary
            clear_partial_summaries(filename)
            save_summary(filename, summary, prompt_type, tldr, key_terms)

            
    return summary

# Saving / Persistence

def save_summary(filename, summary, prompt_type, tldr=None, key_terms=None):
    """This function caches summaries to a file for later retrieval. If a save file already exists and we're saving a new summary for a different prompt type, that gets saved as well as the old summaries. When updating if tldrs or key-terms don't exist the function will backfill them."""

    document_name = os.path.basename(filename).replace(".", "_")
    filepath = f"summaries/{document_name}_summary.json"
    os.makedirs("summaries", exist_ok=True)
    if not os.path.exists(filepath): # Writing a new save file
        # create it.
        saved_summary = {
            "filename": filename,
            "summaries": {
                prompt_type: summary
            }
        }
        if tldr:
            saved_summary["tldr"] = tldr
        if key_terms:
            saved_summary["key_terms"] = key_terms
        with open(filepath, "w") as f:
            json.dump(saved_summary, f, indent = 2)
    else: # Updating existing save file
        with open(filepath, "r") as f:
            saved_summary = json.load(f)

        file_changed = False
        if "summaries" not in saved_summary:
            saved_summary["summaries"] = {}
        if prompt_type not in saved_summary["summaries"]:
            saved_summary["summaries"][prompt_type] = summary
            file_changed = True
        if tldr and "tldr" not in saved_summary:
            saved_summary["tldr"] = tldr
            file_changed = True
        if key_terms and "key_terms" not in saved_summary:
            saved_summary["key_terms"] = key_terms
            file_changed = True

        if file_changed:
            with open(filepath, "w") as f:
                json.dump(saved_summary, f, indent = 2)
    return

def load_summary(filename):

    """This function loads a summary from saved summaries, then returns the saved summary object."""

    document_name = os.path.basename(filename).replace(".", "_")
    filepath = f"summaries/{document_name}_summary.json"
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            saved_summary = json.load(f)
        return saved_summary
    return

def save_partial_summaries(filename, chunk_summaries, prompt_type):
    """This function takes a list of summaries for a chunked document and saves it to a save file under it's respective prompt type key."""

    document_name = os.path.basename(filename).replace(".", "_")
    filepath = f"summaries/{document_name}_summary.json"
    os.makedirs("summaries", exist_ok=True)
    if not os.path.exists(filepath): # Writing a new save file with partial summary
        saved_summary = {
            "filename": filename,
            "partial_summaries": chunk_summaries,
            "partial_prompt_type": prompt_type
        }
        with open(filepath, "w") as f:
            json.dump(saved_summary, f, indent = 2)
    else: # Updating existing save file with partial summary
        with open(filepath, "r") as f:
            saved_summary = json.load(f)

        saved_summary["partial_summaries"] = chunk_summaries
        saved_summary["partial_prompt_type"] = prompt_type

        with open(filepath, "w") as f:
            json.dump(saved_summary, f, indent=2)
    return

def clear_partial_summaries(filename):
    """This function loads a save file and clears any partial summaries left within the file."""

    document_name = os.path.basename(filename).replace(".", "_")
    filepath = f"summaries/{document_name}_summary.json"
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            saved_summary = json.load(f)
        saved_summary.pop("partial_summaries", None)
        saved_summary.pop("partial_prompt_type", None)
        with open(filepath, "w") as f:
            json.dump(saved_summary, f, indent=2)
    return

# UI Input

def get_prompt_type():
    """This function asks the user which prompt type they would like to set for their summarisation and returns that prompt type."""

    # Get prompt type from user
    print("\nSelect prompt type from:")
    prompt_keys = list(SUMMARY_PROMPTS.keys())
    for i, prompt_type in enumerate(prompt_keys):
        print(f"{i+1}. {prompt_type}")
    while True:
        prompt_type_choice = input("Choice: ")
        if prompt_type_choice.isdigit() and 1 <= int(prompt_type_choice) <= len(prompt_keys):
            prompt_type = prompt_keys[int(prompt_type_choice) - 1]
            break
        print("Invalid choice, try again.")
    return prompt_type

# Program Flows

def summarise_flow(client, console, filename):
    """This function handles the entire document summarisation flow. It takes a filename and handles all the processes required to summarise the document and continue that route of the program."""

    # Extract doc text
    try:
        document = get_document(filename)
    except (FileNotFoundError, pymupdf.FileNotFoundError, PackageNotFoundError):
        print(f"File not found.")
        return
    except ValueError as e:
        print(e)
        return

    prompt_type = get_prompt_type() # get prompt type from the user
    summary = get_or_generate_summary(client, filename, document, prompt_type, False) # TODO Make extended thinking an option for the initial summary.
    if not summary:
        return
    input("\nPress Enter to continue...")

    post_summary_menu(client, console, filename, document, prompt_type, summary)
    return

def browse_flow(client, console):
    """This function orchestrates the entire open past summary flow. If a user knows they've summarised a document before they can choose to re-open that summary and skip straight to the post summary options."""

    if not os.path.exists("summaries"): # Check if folder exists
        print("\nNo summaries saved")
        return
    
    filepaths = os.listdir("summaries") # Get all filenames in folder
    if not filepaths: # if no save files then exit
        print("\nNo summaries saved.")
        return

    # Display file choices
    print("\nSaved Summaries")
    print("---------------")
    loaded_summaries = []
    for i, filepath in enumerate(filepaths):
        filename = f"summaries/{filepath}"
        with open(filename, "r") as f:
            saved_summary = json.load(f)
        if "summaries" not in saved_summary:
            continue
        print(f"{i+1}. {saved_summary['filename']}")
        loaded_summaries.append(saved_summary)
    print(f"{i+2}. Back to main menu")
    print(f"{i+3}. Quit")

    # Get input and load summary or quit.
    back_to_main = False
    while True:
        filename_choice = input("Choice: ")
        if filename_choice.isdigit() and 1 <= int(filename_choice) <= len(loaded_summaries):
            saved_summary = loaded_summaries[int(filename_choice) - 1]
            break
        elif filename_choice.isdigit() and int(filename_choice) == len(loaded_summaries) + 1: # back to main menu
            back_to_main = True
            break
        elif filename_choice.isdigit() and int(filename_choice) == len(loaded_summaries) + 2: # quit app
            print("\nExiting...")
            exit(0)
        else:
            print("Invalid choice, try again.")
    if back_to_main:
        return

    # Set state after load
    filename = saved_summary["filename"]
    prompt_type = list(saved_summary["summaries"].keys())[0]
    summary = saved_summary["summaries"][prompt_type]
    tldr = saved_summary.get("tldr")
    key_terms = saved_summary.get("key_terms")
    display_summary_info(tldr, key_terms)
    input("\nPress Enter to continue...")

    try:
        document = get_document(filename)
    except (FileNotFoundError, pymupdf.FileNotFoundError, PackageNotFoundError):
        print(f"File not found.")
        return
    except ValueError as e:
        print(e)
        return

    # Call post summary loop.
    post_summary_menu(client, console, filename, document, prompt_type, summary)
    return

def qa_mode(client, console, filename, summary, prompt_type, extended_thinking):
    """This function lets the user ask questions about a summarised document. It takes a summary, prompt_type and its filename and uses the summary as the conversation context."""

    if extended_thinking == True:
        thinking_budget = THINKING_BUDGET
    else:
        thinking_budget = None
    system_prompt = QANDA_PROMPTS[prompt_type] + f"\n\nDocument Summary: {summary}"
    print(f"\nQ&A Mode: {filename} ({prompt_type})")
    print("-------------------------------------------------------------------")
    print("type 'quit' to return to menu")
    messages = []
    while True:
        user_message = input("\n>> ")
        if user_message.lower().strip() == "quit":
            print("Returning to post summary menu")
            break
        if not user_message.strip():
            print("Please enter a question.")
            continue
        current_user_message = {"role": "user", "content": user_message}
        messages.append(current_user_message)
        try:
            response = get_claude_response(client, messages, system_prompt, thinking_budget=thinking_budget)
            message = response.parse()
            message_text = next(message_block.text for message_block in message.content if message_block.type == "text")
            print()
            console.print(Markdown(message_text))
            current_assistant_message = {"role": "assistant", "content": message_text}
            messages.append(current_assistant_message)
        except API_ERRORS as e:
            print(f"\nFailed to get response from assistant. {e}")
            print("There has been no API cost for this question. Please try again.")
            messages.pop()
    return

def post_summary_menu(client, console, filename, document, prompt_type, summary):
    """This function displays a menu to the user that allows them to choose options after summarising a document of what they would like to do with the summary.
    It takes the state variables of a summary as parameters. Then offers to print the full summary, change the summary type, enter Q&A mode or leave the menu."""

    extended_thinking = False
    while True:
        print(f"\nSummary: {filename} ({prompt_type})")
        print("-------------------------------------------------------------------")
        print("1. Read full summary.")
        print("2. Change summary type.")
        print("3. Enter Q&A mode.")
        if extended_thinking:
            print("4. Extended thinking [ON]")
        else:
            print("4. Extended thinking [OFF]")
        print("5. Back to main menu.")
        print("6. Quit")
        choice = input("Enter the number that matches your chosen option: ")
        if choice == "1": # Print Summary
            print()
            console.print(Markdown(summary))
            input("\nPress Enter to continue...")
        elif choice == "2": # Change summary type
            new_prompt_type = get_prompt_type()
            result = get_or_generate_summary(client, filename, document, new_prompt_type, extended_thinking)
            if result:
                summary = result
                prompt_type = new_prompt_type
            input("\nPress Enter to continue...")
        elif choice == "3": # Enter Q&A mode
            qa_mode(client, console, filename, summary, prompt_type, extended_thinking)
        elif choice == "4": # Toggle extended thinking
            if extended_thinking:
                extended_thinking = False
                print("\nExtended thinking toggled OFF")
            else:
                extended_thinking = True
                print("\nExtended thinking toggled ON")
        elif choice == "5": # Back to main menu
            break
        elif choice == "6": # Quit
            print("\nExiting...")
            exit(0)
        else:
            print("Invalid option, please enter an option in the below list")
    return

# Main

client = anthropic.Anthropic() # connect to anthropic API
console = Console() # instantiate console formatting tools

parser = argparse.ArgumentParser()
parser.add_argument("filename", nargs="?", default=None)
parser.add_argument("--debug", action="store_true")
args = parser.parse_args()
DEBUG = args.debug
cli_filename = args.filename

while True:
    # Main menu
    print("\nDocument Analyzer")
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
            filename = input("\nEnter filename: ")

        summarise_flow(client, console, filename) # summarise / load summary and enter post summary menu
    elif choice == "2": # Browse
        browse_flow(client, console)
    elif choice == "3": # Quit
        print("\nExiting...")
        exit(0)
    else: # Invalid Input
        print("Invalid option, please enter an option in the below list")