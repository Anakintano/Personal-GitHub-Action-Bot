# Remaining test coverage

The regression tests in `tests/` cover private-key redaction, persistent inbox entries, and ordering of successful run attempts.

- Simulate GitHub API responses for paginated jobs and comments, fork PR head matches, ambiguous and missing PRs, failed downloads, rate limits, reruns, and in-place success edits. Verify that successful steps are never downloaded and signed downloads never receive the installation token.
- Check log trimming with long, multiline, ANSI, timestamped, and credential-bearing output. Verify error context, whole evidence lines, redaction, and the shared byte budget.
- Check model output rejection for malformed JSON, invented evidence, and low confidence. Exercise rules and classifier inference with known categories. Keep held-out model evaluation separate from software tests.
- Exercise webhook signature and allowlist checks, duplicate and overlapping deliveries, success after failure, daily caps, SQLite recovery, and Jenkins authentication.
- After authorization, create a demo repository and walk one failing PR through comment creation, another failure through comment update, and a successful rerun through the fixed state. Check service metrics and dashboard against those events.
