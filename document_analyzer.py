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
import os
import json

load_dotenv()

MODEL_PRICING = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-opus-4-5": {"input": 5.00, "output": 25.00}
}

MODEL = "claude-haiku-4-5"
CHUNK_SIZE = 40000
CHARS_PER_TOKEN = 4

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
            if clean_split_point == -1: # if no delimeters found split on the approximate split
                clean_split_point = split_point
            chunk = document[current_position:clean_split_point] # fill chunk
            current_position = clean_split_point + 1
        else: # if chunk past the end, set chunk to the end
            chunk = document[current_position:]
            current_position = len(document)
        chunked_document.append(chunk) # add chunk to list
    return chunked_document

def display_summary_info(tldr, key_terms, input_cost=None, output_cost=None):
    """This function displays document summary information. It takes individual info items and displays them if the info is available."""

    if tldr:
        print(f"\nTLDR: {tldr}")
    if key_terms:
        print(f"\nKey Terms: {', '.join(key_terms)}")
    if input_cost and output_cost:
        total_cost = input_cost + output_cost
        print(f"\nCost Breakdown\n--------------------\nInput: ${input_cost:.6f}\nOutput: ${output_cost:.6f}\nTotal: ${total_cost:.6f}")
    return

def get_claude_response(client, messages, system_prompt, temperature=1.0, model=MODEL, max_tokens=4096, output_config=None):
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
    attempts = 3
    for attempt in range(attempts):
        try:
            message = client.messages.create(**kwargs)
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

def summarise_document(client, document, prompt_type):
    """This function takes a document and summarises it with a chosen prompt type"""

    # Chunk document
    chunked_document = chunk_document(document, CHUNK_SIZE)

    summaries = ""
    input_cost = 0
    output_cost = 0

    if len(chunked_document) == 1: # if single chunk
        print("\nSummarizing...")
        system_prompt = SUMMARY_PROMPTS[prompt_type] + SUMMARY_STRUCTURED_OUTPUT_INSTRUCTIONS
        temperature = 0.5
        messages = [
            {
                "role": "user",
                "content": document
            }
        ]
        try:
            response = get_claude_response(client, messages, system_prompt, temperature, output_config=SUMMARY_OUTPUT_CONFIG)
            input_cost, output_cost = calculate_response_cost(response)
            result = json.loads(response.content[0].text)
            summary = result["summary"]
            tldr = result["tldr"]
            key_terms = result["key_terms"]
        except API_ERRORS as e:
            print(f"Failed to summarise: {e}")
            print("There has been no API cost for this summary.")
            return 

    else:
        map_prompt = SUMMARY_PROMPTS[prompt_type]
        temperature = 0.5
        print()
        try:
            for i, chunk in enumerate(chunked_document):
                print(f"Summarizing chunk {i + 1}/{len(chunked_document)}...")
                messages = [
                    {
                        "role": "user",
                        "content": chunk
                    }
                ]
                response = get_claude_response(client, messages, map_prompt, temperature)
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
                return
            else:
                print(f"Attempting partial summary from {i} completed chunks.")
        print(f"\nGenerating final summary...")
        reduce_prompt = SUMMARY_PROMPTS[prompt_type] + SUMMARY_STRUCTURED_OUTPUT_INSTRUCTIONS + REDUCE_INSTRUCTIONS
        messages = [
            {
                "role": "user",
                "content": summaries
            }
        ]
        try:
            response = get_claude_response(client, messages, reduce_prompt, temperature, output_config=SUMMARY_OUTPUT_CONFIG)
            final_input_cost, final_output_cost = calculate_response_cost(response)
            input_cost += final_input_cost
            output_cost += final_output_cost
            result = json.loads(response.content[0].text)
            summary = result["summary"]
            tldr = result["tldr"]
            key_terms = result["key_terms"]
        except API_ERRORS as e:
            print("Failed to combine summaries.")
            print("Displaying successful chunk summaries")
            tldr=None
            key_terms=None
            return summaries, tldr, key_terms, input_cost, output_cost
    return summary, tldr, key_terms, input_cost, output_cost

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

def save_summary(filename, summary, prompt_type, tldr=None, key_terms=None):
    """This function caches summaries to a file for later retrieval."""

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

def get_or_generate_summary(client, filename, document, prompt_type):
    """This function gets a summary and returns it, either by retrieving it from a saved summary or generating a new one."""

    saved_summary = load_summary(filename)
    if saved_summary and prompt_type in saved_summary["summaries"]:
        print("\nCached summary loaded.")
        summary = saved_summary["summaries"][prompt_type]
        tldr = saved_summary.get("tldr")
        key_terms = saved_summary.get("key_terms")
        display_summary_info(tldr, key_terms)
    else:
        summary_result = summarise_document(client, document, prompt_type)
        if not summary_result:
            return
        summary, tldr, key_terms, input_cost, output_cost = summary_result
        display_summary_info(tldr, key_terms, input_cost, output_cost)
        save_summary(filename, summary, prompt_type, tldr, key_terms)
    return summary

def post_summary_menu(client, console, filename, document, prompt_type, summary):
    """This function displays a menu to the user that allows them to choose options after summarising a document of what they would like to do with the summary.
    It takes the state variables of a summary as parameters. Then offers to print the full summary, change the summary type, enter Q&A mode or leave the menu."""

    while True:
        print(f"\nSummary: {filename} ({prompt_type})")
        print("-------------------------------------------------------------------")
        print("1. Read full summary.")
        print("2. Change summary type.")
        print("3. Enter Q&A mode.")
        print("4. Back to main menu.")
        print("5. Quit")
        choice = input("Enter the number that matches your chosen option: ")
        if choice == "1": # Print Summary
            print()
            console.print(Markdown(summary))
            input("\nPress Enter to continue...")
        elif choice == "2": # Change summary type
            prompt_type = get_prompt_type()
            result = get_or_generate_summary(client, filename, document, prompt_type)
            if result:
                summary = result
            input("\nPress Enter to continue...")
        elif choice == "3": # Enter Q&A mode
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
                    response = get_claude_response(client, messages, system_prompt)
                    print()
                    console.print(Markdown(response.content[0].text))
                    current_assistant_message = {"role": "assistant", "content": response.content[0].text}
                    messages.append(current_assistant_message)
                except API_ERRORS as e:
                    print(f"\nFailed to get response from assistant. {e}")
                    print("There has been no API cost for this question. Please try again.")
                    messages.pop()
        elif choice == "4": # Back to main menu
            break
        elif choice == "5": # Quit
            print("\nExiting...")
            exit(0)
        else:
            print("Invalid option, please enter an option in the below list")
    return
# Main

client = anthropic.Anthropic() # connect to anthropic API
console = Console() # instantiate console formatting tools

cli_filename = None
if len(sys.argv) > 1:
    cli_filename = sys.argv[1]

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

        # Extract doc text
        try:
            document = get_document(filename)
        except (FileNotFoundError, pymupdf.FileNotFoundError, PackageNotFoundError):
            print(f"File not found.")
            continue
        except ValueError as e:
            print(e)
            continue

        # Print document stats
        char_count = len(document)
        print(f"\nCharacters: {char_count:,}")
        estimated_tokens = char_count / 4
        print(f"Estimated tokens: {estimated_tokens:,.0f}")

        prompt_type = get_prompt_type() # get prompt type from the user
        summary = get_or_generate_summary(client, filename, document, prompt_type)
        if not summary:
            continue
        input("\nPress Enter to continue...")

        post_summary_menu(client, console, filename, document, prompt_type, summary)
    elif choice == "2": # Browse
        if not os.path.exists("summaries"):
            print("\nNo summaries saved")
            continue
        filepaths = os.listdir("summaries")
        if not filepaths:
            print("\nNo summaries saved.")
            continue
        print("\nSaved Summaries")
        print("---------------")
        loaded_summaries = []
        for i, filepath in enumerate(filepaths):
            filename = f"summaries/{filepath}"
            with open(filename, "r") as f:
                saved_summary = json.load(f)
            print(f"{i+1}. {saved_summary['filename']}")
            loaded_summaries.append(saved_summary)
        print(f"{i+2}. Back to main menu")
        print(f"{i+3}. Quit")
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
            continue
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
            continue
        except ValueError as e:
            print(e)
            continue

        # Call post summary loop.
        post_summary_menu(client, console, filename, document, prompt_type, summary)
    elif choice == "3": # Quit
        print("\nExiting...")
        exit(0)
    else: # Invalid Input
        print("Invalid option, please enter an option in the below list")