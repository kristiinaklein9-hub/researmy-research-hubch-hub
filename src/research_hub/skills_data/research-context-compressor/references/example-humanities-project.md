# Example: humanities project with minimal scaffold

A literature-review project with just `README.md` + `notes/` + a few draft markdown files will produce a manifest like:

```yaml
project_name: "late-ming-print-culture"
research_area: "history of book publishing"
research_question: ""             # left empty — README didn't state one
current_stage: "discovery"
primary_tools: []                 # no code project
key_repositories: []
data_sources: []                  # no data/ directory, intentional
model_components: []
main_entrypoints: []              # no scripts/ either
important_outputs:                # populated from notes/ + drafts/
  - "notes/01-survey.md"
  - "notes/02-method.md"
  - "drafts/intro-v1.md"
paper_or_deliverable: ""
last_updated: "2026-04-26"
```

Empty fields are honest signals to the next AI session that this is a non-code project. They are not failures.
