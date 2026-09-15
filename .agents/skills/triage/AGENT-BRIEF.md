# Implementation briefs

A brief captures the agreed work and its completion criteria. It summarizes the ticket and accepted decisions; it does not override later explicit user or maintainer instructions. For a PR, describe what remains to do on its existing diff.

Include what changes the implementation decision. Stable paths, interfaces and examples can help navigation; label them as current pointers rather than prescribing line-by-line edits. Avoid repeating the whole issue or adding unrelated requirements.

```markdown
## Agent Brief

**Problem:** Current behavior and its impact.
**Desired behavior:** Observable outcome, including relevant failure cases.
**Scope:** Included work and meaningful exclusions.
**Relevant context:** Contracts, interfaces, existing implementation and decisions.
**Acceptance:** Concrete conditions another contributor can verify.
**Validation:** Reproduction or test commands and actual evidence, if available.
**Open decisions:** Only information still needed; omit when resolved.
```

For a cross-module practicum workstream, use the repository's detailed Issue template for ownership, input/output contracts and handoff. A small fix does not need that ceremony. Do not mark work `ready-for-agent` while a required acceptance decision is unresolved.

Post only when publishing a brief is within the requested triage action, using the disclaimer required by the parent skill.
