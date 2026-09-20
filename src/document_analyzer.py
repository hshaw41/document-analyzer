import os
import json
import argparse

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from docx.opc.exceptions import PackageNotFoundError
import pymupdf

import config
from extraction import get_document
from claude_client import get_claude_response, API_ERRORS
from engine import summarise_document, SUMMARY_PROMPTS

load_dotenv()

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

# Q&A system prompts — tone matches the summary preset so answers feel consistent
QANDA_PROMPTS = {
    "simple": "You are a science communicator who explains complex research to general audiences. From a document summary answer questions about the summary using no jargon, simple analogies, and plain language. The goal of this discussion is to gain an understanding of what the document covers and why it matters in less than a 10 minute conversation. Base your answers strictly on the content of the summary. If something is unclear or not covered by the summary, say so rather than speculating.",
    "in_depth": "You are a technical writer who explains research clearly without sacrificing accuracy. From a document summary answer questions about that summary with full technical detail, explaining why each concept, method and result matters. The goal of this conversation is for the user to fully understand the paper's contributions, methods and results. Base your answers strictly on the content of the provided summary of a research paper. If something is unclear or not covered by the summary, say so rather than speculating.",
    "expert": "You are a research scientist discussing a paper with a knowledgeable peer. Provide research-grade answers to the user's questions including limitations, implementation details, comparisons to related work, and mathematical or architectural specifics. The goal of this conversation is to give the user a deep enough understanding to consider implementing or reproducing ideas from the paper. Base your answers strictly on the content of the provided summary of a research paper. If something is unclear or not covered by the summary, say so rather than speculating."
}

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
# Core Summarisation Logic
# ──────────────────────────────────────────────

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
