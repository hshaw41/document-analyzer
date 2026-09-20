import config
import anthropic
import time

API_ERRORS = (anthropic.APIConnectionError, anthropic.APIError, anthropic.APITimeoutError, anthropic.RateLimitError)

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
            kwargs["temperature"] = 1.0

    attempts = 3
    for attempt in range(attempts):
        try:
            message = client.messages.with_raw_response.create(**kwargs)
            return message
        except anthropic.RateLimitError as e:
            last_error = e
            retry_after = e.response.headers.get("retry-after")
            if retry_after:
                time.sleep(float(retry_after))
            else:
                time.sleep(60)

    raise last_error

def calculate_response_cost(response, model=config.MODEL) -> tuple[float, float]:
    """Calculate the input and output cost of an API response in dollars."""
    input_cost = (response.usage.input_tokens / 1_000_000) * config.MODEL_PRICING[model]["input"]
    output_cost = (response.usage.output_tokens / 1_000_000) * config.MODEL_PRICING[model]["output"]
    return input_cost, output_cost