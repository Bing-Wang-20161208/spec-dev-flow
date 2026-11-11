---
api:
  confirmed: null
  needed: null
  mock: null
  real_ready: null
  base_url: ""
  readiness_source: ""
  openapi: []
---
# Implementation Plan: [FEATURE]

**Branch**: `[###-feature-name]` | **Date**: [DATE] | **Spec**: [link]

**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

## Summary

[Primary requirement and intended technical approach.]

## Technical Context

**Language/Version**: [From target repository]

**Primary Dependencies**: [Relevant existing dependencies]

**Testing**: [Commands and approach from target repository]

**Constraints**: [Relevant constraints or unresolved questions]

## Constitution Check

[Confirm applicable principles in `.specify/memory/constitution.md`; list unresolved items.]

## Project Structure

### Documentation (this feature)

```text
specs/[feature]/
├── spec.md
├── plan.md
└── tasks.md
```

### Source Code (target repository)

```text
[Only relevant existing paths in the configured repository]
```

**Structure Decision**: [Repository alias and reason for the selected code location]

## API and Mock Readiness

- **OpenAPI**: [Use `alias:repository/relative/openapi/path` entries in YAML `api.openapi`; cite the source file(s) in this plan.]
- **Interface readiness**: [Record the user's explicit readiness statement and source. Do not infer availability.]
- **Base URL**: [User-provided URL, or leave empty while unavailable.]
- **Mock plan**: [Whether a temporary mock is needed, its scope, and cleanup task if used.]

## Execution Plan

[Summarize task order, dependencies, model-selection rationale, mock/API strategy, and test scope. Detailed tasks and live status remain in `tasks.md`.]

## Complexity Tracking

| Decision or exception | Reason | User confirmation needed |
|---|---|---|
| [None or a specific item] | [Reason] | [Yes/No] |
