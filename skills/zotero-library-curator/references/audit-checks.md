# Audit checks

For each request, pick the relevant subset.

## Duplicate DOI scan

- Group items by normalized DOI.
- Report any DOI with > 1 Zotero item.
- For each duplicate group, suggest which item to keep (the one with more notes / tags / attachments) and which to merge or trash.

## Orphan-tag scan

For each item in a research-hub cluster:

- Required tags (post v0.61): `research-hub`, `cluster/<slug>`, optionally `category/<arxiv-cat>`, `type/<doc-type>`, `src/<backend>`.
- Report items missing `research-hub` or `cluster/<slug>`.
- Suggested fix: `research-hub zotero backfill --tags`.

## Cross-collection cluster mismatch

- For each item, compare `cluster/<slug>` tag vs Zotero collection membership.
- Report items whose tag and collection disagree.
- Suggested fix: `research-hub clusters rebind --emit` then `--apply`.

## Tag hygiene report

- Top 50 most-used tags + count.
- Tags used < 3 times (potential typos).
- Tags that look like near-duplicates (e.g. `agent-based-modelling` vs `agent-based-modeling`).
- Suggested fix: human review + manual rename via Zotero desktop or `zotero-skills` batch update.

## Collection bloat / sparsity

- Collections with > 200 items (consider splitting).
- Collections with < 3 items (consider merging or deleting).
- Empty collections.
