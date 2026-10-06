import json
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

import config
from claude_client import get_claude_response, calculate_response_cost, API_ERRORS

# Summarisation system prompts, keyed by depth level
SUMMARY_PROMPTS = {
    "simple": "You are a science communicator who explains complex research to general audiences. summarise this document using no jargon, simple analogies, and plain language. The goal is for the reader to understand what the document covers and why it matters in less than five minutes. Base your summary strictly on the content of the provided document. If something is unclear or not covered in the document, say so rather than speculating.",
    "in_depth": "You are a technical writer who explains research clearly without sacrificing accuracy. summarise this document with full technical detail, explaining why each concept, method, and result matters. The goal is for the reader to fully understand the paper's contributions, methods, and results. Base your summary strictly on the content of the provided document. If something is unclear or not covered in the document, say so rather than speculating.",
    "expert": "You are a research scientist summarizing a paper for a knowledgeable peer. Provide a research-grade summary including limitations, implementation details, comparisons to related work, and mathematical or architectural specifics. The goal is to give the reader a deep enough understanding to consider implementing or reproducing ideas from the paper. Base your summary strictly on the content of the provided document. If something is unclear or not covered in the document, say so rather than speculating."
}

# Instructions appended to prompts for the reduce step and structured output
REDUCE_INSTRUCTIONS = " You will receive multiple summaries of sections of a large document. Combine them into one coherent summary."
SUMMARY_STRUCTURED_OUTPUT_INSTRUCTIONS = "In the tldr field place a one sentence overview of the document. In the key_terms field, list the technical terms and concepts of the document. Place the full summary in the summary field."

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


@dataclass
class SummaryStats:
    input_tokens: int = 0
    output_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0
    chunks: int = 0


@dataclass
class SummaryResult:
    status: str                              # "complete" | "partial" | "failed" — checked first by every caller
    error: str | None = None                 # str(e); None on success; display-only
    failed_chunk: int | None = None          # failed chunk index; tells caller which chunk errored
    summary: str | None = None
    tldr: str | None = None
    key_terms: list[str] | None = None
    chunk_summaries: list[str] | None = None # None unless partial; the resume payload, consumed only by services
    stats: SummaryStats = field(default_factory=SummaryStats)
    from_cache: bool = False                 # set True when services builds the result from a cache hit


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

def summarise_document(client, document, prompt_type, extended_thinking, saved_chunk_summaries=None):
    """Summarise a document using a map-reduce approach for multi-chunk documents.
       Returns SummaryResult dataclass
    """

    summary_stats = SummaryStats()
    summary_result = SummaryResult(status = "complete", stats = summary_stats)

    chunked_document = chunk_document(document, config.CHUNK_SIZE)
    summary_stats.chunks = len(chunked_document)

    # Restore progress from a previous failed attempt if available
    if saved_chunk_summaries:
        summary_result.chunk_summaries = saved_chunk_summaries
        summaries = "\n\n".join(saved_chunk_summaries)
        start_index = len(saved_chunk_summaries)
    else:
        summary_result.chunk_summaries = []
        summaries = ""
        start_index = 0

    thinking_budget = config.THINKING_BUDGET if extended_thinking else None

    # ── Single-chunk path (no map-reduce needed) ──

    if summary_stats.chunks == 1:
        system_prompt = SUMMARY_PROMPTS[prompt_type] + SUMMARY_STRUCTURED_OUTPUT_INSTRUCTIONS
        

        try:

            progress = Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                TimeElapsedColumn(),
            )

            with progress:
                task = progress.add_task("Summarising...", total=None)

                messages = [{"role": "user", "content": document}]
                response = get_claude_response(client, messages, system_prompt, output_config=SUMMARY_OUTPUT_CONFIG, thinking_budget=thinking_budget)

                message = response.parse()
                message_text = next(block.text for block in message.content if block.type == "text")

                summary_stats.input_cost, summary_stats.output_cost = calculate_response_cost(message)
                summary_stats.input_tokens = message.usage.input_tokens
                summary_stats.output_tokens = message.usage.output_tokens

                result = json.loads(message_text)
                summary_result.summary = result["summary"]
                summary_result.tldr = result["tldr"]
                summary_result.key_terms = result["key_terms"]
                summary_result.status = "complete"

        except API_ERRORS as e:
            summary_result.status = "failed"
            summary_result.error = str(e)
            return summary_result

    # ── Multi-chunk path (map individual chunks, then reduce) ──

    else:
        map_prompt = SUMMARY_PROMPTS[prompt_type]
        tokens_remaining = 50000
        reset_time_utc = datetime.now(timezone.utc)

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
                    task = progress.add_task(f"Resuming from chunk {start_index + 1}...", total=summary_stats.chunks)
                    progress.update(task, completed=start_index)
                else:
                    task = progress.add_task("Starting...", total=summary_stats.chunks)

                for i, chunk in enumerate(chunked_document):
                    if i < start_index:
                        continue

                    progress.update(task, description=f"Summarising Chunk {i + 1}/{summary_stats.chunks}")
                    
                    # Proactive rate limit wait — sleep if remaining tokens are too low for the next chunk
                    estimated_next_tokens = (len(chunk) + len(map_prompt)) / 4
                    if tokens_remaining < estimated_next_tokens:
                        wait_seconds = (reset_time_utc - datetime.now(timezone.utc)).total_seconds()
                        if wait_seconds > 0:
                            time.sleep(wait_seconds)

                    messages = [{"role": "user", "content": chunk}]
                    response = get_claude_response(client, messages, map_prompt, thinking_budget=thinking_budget)
                    message = response.parse()
                    message_text = next(block.text for block in message.content if block.type == "text")

                    summary_result.chunk_summaries.append(message_text)
                    summaries += "\n\n" + message_text

                    # Accumulate costs and tokens
                    chunk_input_cost, chunk_output_cost = calculate_response_cost(message)
                    summary_stats.input_cost += chunk_input_cost
                    summary_stats.output_cost += chunk_output_cost
                    summary_stats.input_tokens += message.usage.input_tokens
                    summary_stats.output_tokens += message.usage.output_tokens

                    # Read rate limit headers for proactive waiting on next iteration
                    tokens_remaining = int(response.headers.get("anthropic-ratelimit-input-tokens-remaining"))
                    reset_time_string = response.headers.get("anthropic-ratelimit-input-tokens-reset")
                    reset_time_utc = datetime.fromisoformat(reset_time_string)

                    progress.advance(task)

        except API_ERRORS as e:
            summary_result.error = str(e)
            summary_result.failed_chunk = i + 1
            if not summary_result.chunk_summaries:
                summary_result.status = "failed"
                return summary_result

        # Reduce step — combine all chunk summaries into a single final summary
        summary_result.failed_chunk
        reduce_prompt = SUMMARY_PROMPTS[prompt_type] + SUMMARY_STRUCTURED_OUTPUT_INSTRUCTIONS + REDUCE_INSTRUCTIONS
        messages = [{"role": "user", "content": summaries}]

        try:
            
            progress = Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                TimeElapsedColumn(),
            )

            with progress:

                task = progress.add_task("Generating final summary...", total=None)

                estimated_next_tokens = (len(summaries) + len(reduce_prompt)) / 4
                if tokens_remaining < estimated_next_tokens:
                    wait_seconds = (reset_time_utc - datetime.now(timezone.utc)).total_seconds()
                    if wait_seconds > 0:
                        time.sleep(wait_seconds)

                response = get_claude_response(client, messages, reduce_prompt, output_config=SUMMARY_OUTPUT_CONFIG, thinking_budget=thinking_budget)
                message = response.parse()
                message_text = next(block.text for block in message.content if block.type == "text")

                final_input_cost, final_output_cost = calculate_response_cost(message)
                summary_stats.input_cost += final_input_cost
                summary_stats.output_cost += final_output_cost
                summary_stats.input_tokens += message.usage.input_tokens
                summary_stats.output_tokens += message.usage.output_tokens

                result = json.loads(message_text)
                summary_result.summary = result["summary"]
                summary_result.tldr = result["tldr"]
                summary_result.key_terms = result["key_terms"]
                summary_result.status = "complete"
                summary_result.chunk_summaries = None
                summary_result.error = None


        except API_ERRORS as e:
            summary_result.status = "partial"
            summary_result.error = str(e)
            return summary_result
        
    return summary_result
