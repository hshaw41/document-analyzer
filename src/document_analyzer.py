import time
import os
import json
import argparse
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from docx.opc.exceptions import PackageNotFoundError
import pymupdf

import config
from extraction import get_document

load_dotenv()

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

# Summarisation system prompts, keyed by depth level
SUMMARY_PROMPTS = {
    "simple": "You are a science communicator who explains complex research to general audiences. summarise this document using no jargon, simple analogies, and plain language. The goal is for the reader to understand what the document covers and why it matters in less than five minutes. Base your summary strictly on the content of the provided document. If something is unclear or not covered in the document, say so rather than speculating.",
    "in_depth": "You are a technical writer who explains research clearly without sacrificing accuracy. summarise this document with full technical detail, explaining why each concept, method, and result matters. The goal is for the reader to fully understand the paper's contributions, methods, and results. Base your summary strictly on the content of the provided document. If something is unclear or not covered in the document, say so rather than speculating.",
    "expert": "You are a research scientist summarizing a paper for a knowledgeable peer. Provide a research-grade summary including limitations, implementation details, comparisons to related work, and mathematical or architectural specifics. The goal is to give the reader a deep enough understanding to consider implementing or reproducing ideas from the paper. Base your summary strictly on the content of the provided document. If something is unclear or not covered in the document, say so rather than speculating."
}

# Q&A system prompts — tone matches the summary preset so answers feel consistent
QANDA_PROMPTS = {
    "simple": "You are a science communicator who explains complex research to general audiences. From a document summary answer questions about the summary using no jargon, simple analogies, and plain language. The goal of this discussion is to gain an understanding of what the document covers and why it matters in less than a 10 minute conversation. Base your answers strictly on the content of the summary. If something is unclear or not covered by the summary, say so rather than speculating.",
    "in_depth": "You are a technical writer who explains research clearly without sacrificing accuracy. From a document summary answer questions about that summary with full technical detail, explaining why each concept, method and result matters. The goal of this conversation is for the user to fully understand the paper's contributions, methods and results. Base your answers strictly on the content of the provided summary of a research paper. If something is unclear or not covered by the summary, say so rather than speculating.",
    "expert": "You are a research scientist discussing a paper with a knowledgeable peer. Provide research-grade answers to the user's questions including limitations, implementation details, comparisons to related work, and mathematical or architectural specifics. The goal of this conversation is to give the user a deep enough understanding to consider implementing or reproducing ideas from the paper. Base your answers strictly on the content of the provided summary of a research paper. If something is unclear or not covered by the summary, say so rather than speculating."
}

# Instructions appended to prompts for the reduce step and structured output
REDUCE_INSTRUCTIONS = " You will receive multiple summaries of sections of a large document. Combine them into one coherent summary."
SUMMARY_STRUCTURED_OUTPUT_INSTRUCTIONS = "In the tldr field place a one sentence overview of the document. In the key_terms field, list the technical terms and concepts of the document. Place the full summary in the summary field."

# Tuple of API exceptions caught throughout the app for graceful error handling
API_ERRORS = (anthropic.APIConnectionError, anthropic.APIError, anthropic.APITimeoutError, anthropic.RateLimitError)

# JSON schema enforcing structured output from the summarisation API call
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

# ──────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────

def chunk_document(document, chunk_size):
    """Split a document string into chunks of approximately chunk_size tokens.

    Tries to break on paragraph boundaries first, then newlines, then spaces.
    Falls back to a hard split if no clean break point is found.
    """
    chunked_document = []
    current_position = 0

    while current_position < len(document):
        split_point = current_position + (chunk_size * config.CHARS_PER_TOKEN)

        if split_point < len(document):
            # Try progressively less ideal break points
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
            # Remaining text fits in one chunk
            chunk = document[current_position:]
            current_position = len(document)

        chunked_document.append(chunk)

    return chunked_document

# ──────────────────────────────────────────────
# CLI Display
# ──────────────────────────────────────────────

def display_summary_info(tldr, key_terms):
    """Print the TLDR and key terms to the terminal if available."""
    if tldr:
        print(f"\nTLDR: {tldr}")
    if key_terms:
        print(f"\nKey Terms: {', '.join(key_terms)}")


def display_debug_info(model, char_count, estimated_tokens, input_tokens, output_tokens, chunks, input_cost, output_cost):
    """Print a debug block showing model, token counts, chunk count and cost breakdown."""
    print("\n[DEBUG]")
    print(f"Model: {model}")
    print(f"Characters: {char_count}")
    print(f"Estimated Tokens: {estimated_tokens}")
    if input_tokens and output_tokens:
        print(f"Actual Tokens: {input_tokens} in / {output_tokens} out")
    if chunks:
        print(f"Chunks: {chunks}")
    if input_cost and output_cost:
        print(f"Cost: ${input_cost:.6f} in / ${output_cost:.6f} out / ${(input_cost + output_cost):.6f} total")

# ──────────────────────────────────────────────
# API
# ──────────────────────────────────────────────

def get_claude_response(client, messages, system_prompt, temperature=1.0, model=config.MODEL, max_tokens=4096, output_config=None, thinking_budget=None):
    """Send a request to the Claude API and return the raw response.

    Returns the raw response object (use .parse() for the message, .headers for rate limit info).
    Retries up to 3 times on rate limit errors, using the retry-after header when available.
    When thinking_budget is set, extended thinking is enabled and max_tokens is increased accordingly.
    """
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
        kwargs["max_tokens"] = max_tokens + thinking_budget
        if temperature != 1.0:
            print("Warning: can't have temperature below 1.0 when thinking is enabled. Setting back to 1.0")
            kwargs["temperature"] = 1.0

    attempts = 3
    for attempt in range(attempts):
        try:
            message = client.messages.with_raw_response.create(**kwargs)
            return message
        except anthropic.RateLimitError as e:
            last_error = e
            print(f"API Rate limit error. Attempt {attempt + 1}/{attempts} failed. Retrying...")
            retry_after = e.response.headers.get("retry-after")
            if retry_after:
                time.sleep(float(retry_after))
            else:
                time.sleep(60)

    raise last_error


def calculate_response_cost(response, model=config.MODEL):
    """Calculate the input and output cost of an API response in dollars."""
    input_cost = (response.usage.input_tokens / 1_000_000) * config.MODEL_PRICING[model]["input"]
    output_cost = (response.usage.output_tokens / 1_000_000) * config.MODEL_PRICING[model]["output"]
    return input_cost, output_cost

# ──────────────────────────────────────────────
# Core Summarisation Logic
# ──────────────────────────────────────────────

def summarise_document(client, document, prompt_type, extended_thinking, saved_chunk_summaries=None):
    """Summarise a document using a map-reduce approach for multi-chunk documents.

    Returns a 9-tuple on success or partial failure:
        (summary, tldr, key_terms, input_tokens, output_tokens, chunks, input_cost, output_cost, chunk_summaries)

    chunk_summaries is None on success, or a list of individual chunk summary strings on partial failure
    (used by the caller to save progress for resume). Returns None on total failure.
    """
    chunked_document = chunk_document(document, config.CHUNK_SIZE)
    chunks = len(chunked_document)

    # Track individual chunk summaries for resume-on-failure support
    chunk_summaries = []

    # Restore progress from a previous failed attempt if available
    if saved_chunk_summaries:
        chunk_summaries = saved_chunk_summaries
        summaries = "\n\n".join(saved_chunk_summaries)
        start_index = len(saved_chunk_summaries)
    else:
        summaries = ""
        start_index = 0

    # Running totals for cost and token tracking
    input_cost = 0
    output_cost = 0
    input_tokens = 0
    output_tokens = 0

    thinking_budget = config.THINKING_BUDGET if extended_thinking else None

    # ── Single-chunk path (no map-reduce needed) ──

    if chunks == 1:
        system_prompt = SUMMARY_PROMPTS[prompt_type] + SUMMARY_STRUCTURED_OUTPUT_INSTRUCTIONS
        messages = [{"role": "user", "content": document}]

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
            message_text = next(block.text for block in message.content if block.type == "text")
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
            return None

    # ── Multi-chunk path (map individual chunks, then reduce) ──

    else:
        map_prompt = SUMMARY_PROMPTS[prompt_type]
        tokens_remaining = 50000
        reset_time_utc = datetime.now(timezone.utc)

        print()

        # Map step — summarise each chunk individually
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
                    task = progress.add_task("Starting...", total=chunks)

                for i, chunk in enumerate(chunked_document):
                    if i < start_index:
                        continue

                    progress.update(task, description=f"Summarising Chunk {i + 1}/{chunks}")
                    messages = [{"role": "user", "content": chunk}]

                    # Proactive rate limit wait — sleep if remaining tokens are too low for the next chunk
                    estimated_next_tokens = (len(chunk) + len(map_prompt)) / 4
                    if tokens_remaining < estimated_next_tokens:
                        wait_seconds = (reset_time_utc - datetime.now(timezone.utc)).total_seconds()
                        if wait_seconds > 0:
                            time.sleep(wait_seconds)

                    response = get_claude_response(client, messages, map_prompt, thinking_budget=thinking_budget)
                    progress.advance(task)

                    message = response.parse()
                    message_text = next(block.text for block in message.content if block.type == "text")
                    chunk_summaries.append(message_text)
                    summaries += "\n\n" + message_text

                    # Accumulate costs and tokens
                    chunk_input_cost, chunk_output_cost = calculate_response_cost(message)
                    input_cost += chunk_input_cost
                    output_cost += chunk_output_cost
                    input_tokens += message.usage.input_tokens
                    output_tokens += message.usage.output_tokens

                    # Read rate limit headers for proactive waiting on next iteration
                    tokens_remaining = int(response.headers.get("anthropic-ratelimit-input-tokens-remaining"))
                    reset_time_string = response.headers.get("anthropic-ratelimit-input-tokens-reset")
                    reset_time_utc = datetime.fromisoformat(reset_time_string)

        except API_ERRORS as e:
            print(f"Failed on chunk {i + 1}/{chunks}")
            if not chunk_summaries:
                print("No chunks summarised.")
                print("There has been no API cost for this summary.")
                return None
            else:
                print(f"Attempting partial summary from {i} completed chunks.")

        # Reduce step — combine all chunk summaries into a single final summary
        reduce_prompt = SUMMARY_PROMPTS[prompt_type] + SUMMARY_STRUCTURED_OUTPUT_INSTRUCTIONS + REDUCE_INSTRUCTIONS
        messages = [{"role": "user", "content": summaries}]

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
                    wait_seconds = (reset_time_utc - datetime.now(timezone.utc)).total_seconds()
                    if wait_seconds > 0:
                        time.sleep(wait_seconds)

                response = get_claude_response(client, messages, reduce_prompt, output_config=SUMMARY_OUTPUT_CONFIG, thinking_budget=thinking_budget)

            message = response.parse()
            message_text = next(block.text for block in message.content if block.type == "text")
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
            return summaries, None, None, input_tokens, output_tokens, chunks, input_cost, output_cost, chunk_summaries

    return summary, tldr, key_terms, input_tokens, output_tokens, chunks, input_cost, output_cost, None


def get_or_generate_summary(client, filename, document, prompt_type, extended_thinking, debug):
    """Retrieve a cached summary or generate a new one.

    Checks for a cached summary first. If not found, checks for partial chunk summaries
    from a previously failed attempt and resumes from where it left off. Handles all
    persistence (saving summaries, saving/clearing partial progress).

    Returns the summary string, or None on failure.
    """
    char_count = len(document)
    estimated_tokens = char_count / 4

    saved_summary = load_summary(filename)

    # Check for a fully cached summary matching this prompt type
    if saved_summary and "summaries" in saved_summary and prompt_type in saved_summary["summaries"]:
        print("\nCached summary loaded.")
        summary = saved_summary["summaries"][prompt_type]
        tldr = saved_summary.get("tldr")
        key_terms = saved_summary.get("key_terms")
        display_summary_info(tldr, key_terms)
        return summary

    # Check for partial progress from a previous failed attempt
    saved_chunk_summaries = None
    if saved_summary and "partial_summaries" in saved_summary and saved_summary.get("partial_prompt_type") == prompt_type:
        saved_chunk_summaries = saved_summary["partial_summaries"]
        print(f"\nResuming summarisation from chunk {len(saved_chunk_summaries)}.")

    # Generate a new summary (with or without resumed progress)
    summary_result = summarise_document(client, document, prompt_type, extended_thinking, saved_chunk_summaries)
    if not summary_result:
        return None

    summary, tldr, key_terms, input_tokens, output_tokens, chunks, input_cost, output_cost, chunk_summaries = summary_result

    display_summary_info(tldr, key_terms)
    if debug:
        display_debug_info(config.MODEL, char_count, estimated_tokens, input_tokens, output_tokens, chunks, input_cost, output_cost)

    # Save progress — either partial (for resume) or complete
    if chunk_summaries:
        save_partial_summaries(filename, chunk_summaries, prompt_type)
    else:
        clear_partial_summaries(filename)
        save_summary(filename, summary, prompt_type, tldr, key_terms)

    return summary

# ──────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────

def save_summary(filename, summary, prompt_type, tldr=None, key_terms=None):
    """Save a completed summary to the JSON cache file.

    Creates a new file if none exists. If updating an existing file, adds the new
    prompt type without overwriting other cached summaries. Backfills TLDR and
    key terms if they don't already exist in the file.
    """
    document_name = os.path.basename(filename).replace(".", "_")
    filepath = f"summaries/{document_name}_summary.json"
    os.makedirs("summaries", exist_ok=True)

    if not os.path.exists(filepath):
        saved_summary = {
            "filename": filename,
            "summaries": {prompt_type: summary}
        }
        if tldr:
            saved_summary["tldr"] = tldr
        if key_terms:
            saved_summary["key_terms"] = key_terms
        with open(filepath, "w") as f:
            json.dump(saved_summary, f, indent=2)
    else:
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
                json.dump(saved_summary, f, indent=2)


def load_summary(filename):
    """Load a saved summary JSON file. Returns the parsed dict, or None if no file exists."""
    document_name = os.path.basename(filename).replace(".", "_")
    filepath = f"summaries/{document_name}_summary.json"
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            return json.load(f)
    return None


def save_partial_summaries(filename, chunk_summaries, prompt_type):
    """Save individual chunk summaries from a failed multi-chunk summarisation for later resume.

    Stored alongside any existing summary data in the same JSON file.
    """
    document_name = os.path.basename(filename).replace(".", "_")
    filepath = f"summaries/{document_name}_summary.json"
    os.makedirs("summaries", exist_ok=True)

    if not os.path.exists(filepath):
        saved_summary = {
            "filename": filename,
            "partial_summaries": chunk_summaries,
            "partial_prompt_type": prompt_type
        }
    else:
        with open(filepath, "r") as f:
            saved_summary = json.load(f)
        saved_summary["partial_summaries"] = chunk_summaries
        saved_summary["partial_prompt_type"] = prompt_type

    with open(filepath, "w") as f:
        json.dump(saved_summary, f, indent=2)


def clear_partial_summaries(filename):
    """Remove partial summary data from a save file after a successful full summarisation."""
    document_name = os.path.basename(filename).replace(".", "_")
    filepath = f"summaries/{document_name}_summary.json"
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            saved_summary = json.load(f)
        saved_summary.pop("partial_summaries", None)
        saved_summary.pop("partial_prompt_type", None)
        with open(filepath, "w") as f:
            json.dump(saved_summary, f, indent=2)

# ──────────────────────────────────────────────
# CLI Input
# ──────────────────────────────────────────────

def get_prompt_type():
    """Prompt the user to select a summary depth level. Returns the chosen key."""
    print("\nSelect prompt type from:")
    prompt_keys = list(SUMMARY_PROMPTS.keys())
    for i, prompt_type in enumerate(prompt_keys):
        print(f"{i + 1}. {prompt_type}")
    while True:
        prompt_type_choice = input("Choice: ")
        if prompt_type_choice.isdigit() and 1 <= int(prompt_type_choice) <= len(prompt_keys):
            return prompt_keys[int(prompt_type_choice) - 1]
        print("Invalid choice, try again.")

# ──────────────────────────────────────────────
# CLI Program Flows
# ──────────────────────────────────────────────

def summarise_flow(client, console, filename, debug):
    """Handle the full summarise-a-document flow: extract text, pick prompt type, generate summary, enter post-summary menu."""
    try:
        document = get_document(filename)
    except (FileNotFoundError, pymupdf.FileNotFoundError, PackageNotFoundError):
        print("File not found.")
        return
    except ValueError as e:
        print(e)
        return

    prompt_type = get_prompt_type()
    # TODO: Make extended thinking an option for the initial summary
    summary = get_or_generate_summary(client, filename, document, prompt_type, False, debug)
    if not summary:
        return

    input("\nPress Enter to continue...")
    post_summary_menu(client, console, filename, document, prompt_type, summary, debug)


def browse_flow(client, console, debug):
    """Let the user pick from previously saved summaries and jump straight to the post-summary menu."""
    if not os.path.exists("summaries"):
        print("\nNo summaries saved")
        return

    filepaths = os.listdir("summaries")
    if not filepaths:
        print("\nNo summaries saved.")
        return

    # Load all valid save files (skip partial-only files with no completed summaries)
    print("\nSaved Summaries")
    print("---------------")
    loaded_summaries = []
    for filepath in filepaths:
        filename = f"summaries/{filepath}"
        with open(filename, "r") as f:
            saved_summary = json.load(f)
        if "summaries" not in saved_summary:
            continue
        loaded_summaries.append(saved_summary)
        print(f"{len(loaded_summaries)}. {saved_summary['filename']}")

    if not loaded_summaries:
        print("No completed summaries found.")
        return

    print(f"{len(loaded_summaries) + 1}. Back to main menu")
    print(f"{len(loaded_summaries) + 2}. Quit")

    # Get selection
    back_to_main = False
    while True:
        filename_choice = input("Choice: ")
        if filename_choice.isdigit() and 1 <= int(filename_choice) <= len(loaded_summaries):
            saved_summary = loaded_summaries[int(filename_choice) - 1]
            break
        elif filename_choice.isdigit() and int(filename_choice) == len(loaded_summaries) + 1:
            back_to_main = True
            break
        elif filename_choice.isdigit() and int(filename_choice) == len(loaded_summaries) + 2:
            print("\nExiting...")
            exit(0)
        else:
            print("Invalid choice, try again.")

    if back_to_main:
        return

    # Restore state from saved summary
    filename = saved_summary["filename"]
    prompt_type = list(saved_summary["summaries"].keys())[0]
    summary = saved_summary["summaries"][prompt_type]
    tldr = saved_summary.get("tldr")
    key_terms = saved_summary.get("key_terms")
    display_summary_info(tldr, key_terms)
    input("\nPress Enter to continue...")

    # Load the original document (needed for re-summarisation and Q&A)
    try:
        document = get_document(filename)
    except (FileNotFoundError, pymupdf.FileNotFoundError, PackageNotFoundError):
        print("File not found.")
        return
    except ValueError as e:
        print(e)
        return

    post_summary_menu(client, console, filename, document, prompt_type, summary, debug)


def qa_mode(client, console, filename, summary, prompt_type, extended_thinking):
    """Interactive Q&A loop grounded in the document summary. Maintains conversation history within the session."""
    thinking_budget = config.THINKING_BUDGET if extended_thinking else None
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

        messages.append({"role": "user", "content": user_message})

        try:
            response = get_claude_response(client, messages, system_prompt, thinking_budget=thinking_budget)
            message = response.parse()
            message_text = next(block.text for block in message.content if block.type == "text")
            print()
            console.print(Markdown(message_text))
            messages.append({"role": "assistant", "content": message_text})
        except API_ERRORS as e:
            print(f"\nFailed to get response from assistant. {e}")
            print("There has been no API cost for this question. Please try again.")
            messages.pop()


def post_summary_menu(client, console, filename, document, prompt_type, summary, debug):
    """Post-summarisation menu: read summary, change type, Q&A, toggle thinking, or exit."""
    extended_thinking = False

    while True:
        thinking_label = "[ON]" if extended_thinking else "[OFF]"
        print(f"\nSummary: {filename} ({prompt_type})")
        print("-------------------------------------------------------------------")
        print("1. Read full summary.")
        print("2. Change summary type.")
        print("3. Enter Q&A mode.")
        print(f"4. Extended thinking {thinking_label}")
        print("5. Back to main menu.")
        print("6. Quit")

        choice = input("Enter the number that matches your chosen option: ")

        if choice == "1":
            print()
            console.print(Markdown(summary))
            input("\nPress Enter to continue...")

        elif choice == "2":
            new_prompt_type = get_prompt_type()
            result = get_or_generate_summary(client, filename, document, new_prompt_type, extended_thinking, debug)
            if result:
                summary = result
                prompt_type = new_prompt_type
            input("\nPress Enter to continue...")

        elif choice == "3":
            qa_mode(client, console, filename, summary, prompt_type, extended_thinking)

        elif choice == "4":
            extended_thinking = not extended_thinking
            state = "ON" if extended_thinking else "OFF"
            print(f"\nExtended thinking toggled {state}")

        elif choice == "5":
            break

        elif choice == "6":
            print("\nExiting...")
            exit(0)

        else:
            print("Invalid option, please enter an option in the below list")

# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():

    client = anthropic.Anthropic()
    console = Console()

    parser = argparse.ArgumentParser(description="Document Analyzer CLI — summarise documents and ask questions about them.")
    parser.add_argument("filename", nargs="?", default=None, help="path to a document file to summarise immediately")
    parser.add_argument("--debug", action="store_true", help="show token counts, costs, and other technical details")
    args = parser.parse_args()
    debug = args.debug
    cli_filename = args.filename

    while True:
        print("\nDocument Analyzer")
        print("-----------------")
        print("1. Summarise a document")
        print("2. Open past summary")
        print("3. Quit")
        choice = input("Enter the number that matches your chosen option: ")

        if choice == "1":
            if cli_filename:
                filename = cli_filename
                cli_filename = None  # consume the CLI argument so it's only used once
            else:
                filename = input("\nEnter filename: ")
            summarise_flow(client, console, filename, debug)

        elif choice == "2":
            browse_flow(client, console, debug)

        elif choice == "3":
            print("\nExiting...")
            exit(0)

        else:
            print("Invalid option, please enter an option in the below list")

if __name__ == "__main__":
    main()
