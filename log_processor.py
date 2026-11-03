import re

ANSI = re.compile(r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-_])")
TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}) "
)
ERROR = re.compile(r"error|failed|exception|exit code", re.IGNORECASE)
AUTH = re.compile(r"\b((?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
SECRET = re.compile(
    r"\b((?:[\w-]*(?:token|secret|password|api[_-]?key))\s*[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)",
    re.IGNORECASE,
)
TOKEN = re.compile(
    r"\b(?:github_pat_[\w]+|gh[pousr]_[A-Za-z0-9]+|(?:sk|nvapi)-[A-Za-z0-9_-]{16,}"
    r"|xox[baprs]-[A-Za-z0-9-]+|(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"
)
PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)


def redact(text: str) -> str:
    """Mask common credential formats and values assigned to secret-like keys."""
    text = PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", text)
    text = AUTH.sub(r"\1[REDACTED]", text)
    text = SECRET.sub(r"\1[REDACTED]", text)
    return TOKEN.sub("[REDACTED]", text)


def trim_log(log: str, *, max_bytes: int = 24_000) -> str:
    """Keep error context from a single failed step, within a byte budget."""
    clean = "\n".join(
        TIMESTAMP.sub("", line) for line in ANSI.sub("", log).splitlines()
    )
    lines = [
        redact(line)
        for line in PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", clean).splitlines()
    ]
    matches = [index for index, line in enumerate(lines) if ERROR.search(line)]
    keep = set()
    for index in matches:
        keep.update(range(max(0, index - 50), min(len(lines), index + 51)))
    if not matches:
        keep.update(range(max(0, len(lines) - 50), len(lines)))

    selected = []
    # shortcut: 24 KB approximates 8k tokens, use model token counts if exact limits matter.
    # Keep matching lines before context so a long stack trace cannot displace the error.
    for index in sorted(
        keep, key=lambda index: (not ERROR.search(lines[index]), -index)
    ):
        size = len(lines[index].encode("utf-8")) + 1
        if size <= max_bytes:
            selected.append(index)
            max_bytes -= size
    return "\n".join(lines[index].removeprefix("##[error]") for index in sorted(selected))
