---
name: call-muse
description: "Send a request to Muse (your Muse.ai coordinator) when you need a second reviewer, a judgment call, or help you're blocked on. Use when pstack-interrogate needs Muse as reviewer, when pstack-arena needs Muse as cross-judge, or when you're stuck and need input."
---

# Call Muse

Muse is your coordinator on Muse.ai — a different model family than your local
Hermes agents. Muse judges, reviews, and synthesizes. When your task needs that
outside perspective, use this skill to send a request and poll for a response.

## When to call

- **Second reviewer:** `pstack-interrogate` wants two independent reviewers.
  You are one; Muse is the other.
- **Cross-judge:** `pstack-arena` wants a judge from a different model family.
  Muse is the different family in the stack.
- **Judgment call:** a decision with real consequences where you want a second mind.
- **Blocked:** you're stuck and need input to proceed.
- **Escalation:** something the human needs to see — Muse will surface it.

Do NOT call Muse for things you can do yourself. It's a scarce resource.

## Request format

Write a Markdown file to `<hermes_home>/muse-inbox/requests/<id>.md`
where `<id>` is a short unique slug, e.g. `fred-20261019-1930-interrogate`.
(Replace `<hermes_home>` with your Hermes home, e.g. `/home/ubuntu/.hermes`.)

```markdown
---
id: fred-20261019-1930-interrogate
from: fred
to: muse
type: review-request | judgment | question | escalation
created: 2026-10-19T19:30:00Z
skill: pstack-interrogate
context: /path/to/artifact/on/the/box
deadline: 2026-10-19T20:30:00Z
---

<body: the actual request — what you need, relevant context, what you've tried>
```

**Types:**
- `review-request` — include the artifact path in `context:` and describe what to review
- `judgment` — state the decision and the options as you see them
- `question` — ask it plainly; include what you've already checked
- `escalation` — describe what the human needs to know and why it's urgent

## Poll for the response

Muse's watcher checks for new requests every minute. After sending,
poll `<hermes_home>/muse-inbox/responses/<id>.response.md`
on your heartbeat cycle. The response format:

```markdown
---
id: <id>
from: muse
to: <you>
created: <timestamp>
status: complete | declined | needs-clarification | escalated
---

<body: Muse's response>
```

**Statuses:**
- `complete` — answered; act on it
- `declined` — won't do it; the body says why
- `needs-clarification` — re-send with more context
- `escalated` — taking it to the human; wait for word

## Timeouts

If no response after 30 minutes, assume Muse is unavailable. Proceed with
your best judgment and note in your result that Muse didn't weigh in.
Never block indefinitely waiting.

## Also see

- `pstack-interrogate/SKILL.md` — when you need Muse as second reviewer
- `pstack-arena/SKILL.md` — when you need Muse as cross-judge
