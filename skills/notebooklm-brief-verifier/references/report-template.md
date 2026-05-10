# NotebookLM brief verification report template

In-conversation report. The skill writes this to chat (no file by default). If the brief is well-attributed and bundle coverage is complete, the report is short — that's a feature, not a bug.

```
## NotebookLM brief verification report

**Brief**: <path>
**Bundle**: <cluster_slug> (<N> sources)

### Source coverage
- Cited in brief: <X> / <N>
- Missed sources: <list of citation keys not mentioned>

### Unsupported claims
- "<claim text>" (line <N> of brief) — no clear source attribution
- ...

### Cross-source contradictions
- "<claim A>" (cites Smith 2024) vs "<claim B>" (cites Jones 2023) — appear to contradict; brief does not flag this
- ...

### Potential overgeneralizations
- "Studies show..." — actually one paper, Smith 2024
- ...

### Spot-checked claims
- "<load-bearing claim>" — reviewed Smith 2024 §3, **supported**
- "<surprising claim>" — reviewed Jones 2023 abstract, **partially supported** (specific to coastal basins, not generalizable)

### Recommended follow-up NotebookLM prompts
- "What does Smith 2024 say about <X> specifically? Cite directly."
- "Compare Smith 2024's claim about <Y> with Jones 2023's findings."

### Verdict
- Reliable for: <broad takeaways, comparison framing>
- Use with caution for: <specific numbers, generalizations>
- Do not cite without spot-check: <list>
```
