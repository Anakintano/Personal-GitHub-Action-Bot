"""Format the PR comment body from trimmed CI evidence."""

from textwrap import indent


def format_failure(failed_step: str, error_line: str) -> str:
    step = indent(failed_step, "    ")
    evidence = (
        indent(error_line, "    ") if error_line else "No error line was retained."
    )
    return f"### CI failed\n\nFailed step\n\n{step}\n\nLog evidence\n\n{evidence}"


def format_fixed(sha: str) -> str:
    return f"✅ fixed in `{sha}`"


def format_diagnosis(
    failed_step: str, error_line: str, category: str, cause: str, fix: str
) -> str:
    return (
        f"{format_failure(failed_step, error_line)}\n\n"
        f"Category: **{category}**\n\n"
        f"Likely cause\n\n{indent(cause, '    ')}\n\n"
        f"Suggested fix\n\n{indent(fix, '    ')}"
    )
