# `.paper/` YAML schemas

Schemas for the two main outputs of `paper-memory-builder`. The third file (`revision_history.yml`) has its own dedicated schema doc at `revision_history_schema.md` because of the append-only audit-trail rules.

## `.paper/claims.yml`

```yaml
claims:
  - id: C1
    text: "Coupled ABM-CAT reduces flood-impact RMSE by 22%."
    evidence_artifacts:
      - "outputs/E2/calibration.csv"
      - "outputs/E2/figure3.png"
    figure_or_table: ["Fig3"]
    status: "draft"          # draft | supported | rejected
    risk: "Reviewer R2 may push back on calibration window."
    sentence_in_manuscript: "...we observe a 22% reduction in RMSE..."
```

**Required per claim:** `id`, `text`, `status`.

**Numbering:** `C1, C2, ...` contiguous. If you regenerate, preserve human-assigned IDs — claim numbers are referenced by other tooling (`academic-writing-skills` pulls them by id).

## `.paper/figures.yml`

```yaml
figures:
  - id: "Fig1"
    file: "outputs/figures/Fig1_study_area.png"
    panels: ["a) site map", "b) gauge locations"]
    key_numbers: ["12 gauges", "1985-2024 record length"]
    supports_claims: []
    caption_in_manuscript: "Figure 1. Study area..."
```

**Required per figure:** `id`, `file`, `supports_claims` (may be `[]`).

## See also

- `revision_history_schema.md` — schema + append-only rules for `revision_history.yml`.
