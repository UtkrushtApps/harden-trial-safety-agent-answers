# Solution Steps

1. Keep the existing FastAPI, PostgreSQL, prompt, policy, model client, and orchestration structure; harden each workflow boundary rather than replacing the service.

2. Treat the database provider policy as authoritative. Ignore environment model overrides for study requests, filter out every unapproved route, and preserve route priority for retries.

3. Reduce model context by converting free-text narratives into a small allowlisted set of canonical clinical facts. Never send participant references, source filenames, raw narratives, names, contact details, addresses, or dates of birth to the provider.

4. Apply privacy redaction to all user-supplied labels, prompts, drafts, reviewed outputs, deterministic fallbacks, and persisted response text. Store exception categories only, never exception messages.

5. Use conservative UTF-8-based token projections and route-specific input/output prices before every model call. Skip calls whose projected commitment does not fit the remaining request budget.

6. Bound provider work to two explicitly counted calls, disable OpenAI SDK retries, apply a request timeout, and cap each completion at 512 output tokens. Count and reserve a call before dispatch so failed calls cannot trigger unbounded retries.

7. Retry drafting only through another approved route. Retain the projected cost reservation for failed calls because exact provider usage may be unavailable.

8. Attempt review only when one call remains and its full projected cost fits the remaining budget. Otherwise return a sanitized draft with a deterministic serious-event section.

9. Generate deterministic privacy-safe serious-event facts from structured fields and canonical clinical concepts. Use these facts as the complete fallback when drafting fails or cannot be afforded, and append them to successful model text so serious events cannot be omitted.

10. Persist a sanitized prompt, prompt version and fingerprint, route/model/location, projected and actual cost decisions, token usage, every attempted or skipped call, degradation reason, call limit, and terminal outcome in `agent_runs`.

11. Keep `/health` independent of provider credentials. Constructing the real client and running invariant checks works without a key, while actual model calls use `OPENAI_API_KEY` from `.env`; missing credentials produce a recorded degraded result rather than leaking an internal error.

12. Run `pytest`, then execute `run.sh`. With a provider key configured, verify normal and budget-limited requests; without a key, verify health succeeds and summary requests return deterministic privacy-safe serious-event facts.

