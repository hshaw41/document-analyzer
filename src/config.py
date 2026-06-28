MODEL = "claude-haiku-4-5"
MODEL_PRICING = { 
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-opus-4-5": {"input": 5.00, "output": 25.00}
} # Per-million-token pricing for each supported model
CHUNK_SIZE = 40000           # target tokens per chunk for long documents
CHARS_PER_TOKEN = 4          # rough character-to-token ratio for estimation
THINKING_BUDGET = 10000      # token budget for extended thinking when enabled
