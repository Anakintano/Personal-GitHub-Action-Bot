from prometheus_client import Counter, Histogram

ANALYSES = Counter(
    "analyses_total",
    "Completed failure analyses by final category and category source.",
    ["category", "source"],
)
LLM_TOKENS = Counter("llm_tokens_total", "Input and output tokens reported by the hosted model.")
LLM_LATENCY = Histogram(
    "llm_latency_seconds",
    "Elapsed seconds for a the hosted model diagnosis.",
    buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 30, 60),
)
RATE_LIMITED = Counter("rate_limited_total", "Requests rejected by a local or upstream rate limit.")
ANALYSIS_ERRORS = Counter("analysis_errors_total", "Failure analyses that could not complete.")
