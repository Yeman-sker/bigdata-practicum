# Domain document routing

Adapt this seed to the repository's actual layout. Route by the task:

- Business vocabulary → existing `CONTEXT.md` or the relevant context from `CONTEXT-MAP.md`.
- Service or module boundaries → architecture documentation.
- Schema, event or API changes → the current contract and its accepted ADRs.
- Runtime verification → the runbook.

Do not require all background documents before every edit. Missing optional context does not block exploration; a missing contract necessary to implement the requested behavior may require clarification.

Keep the existing glossary's terms and follow explicit ADR supersession. Record new concepts or decisions when they are actually agreed, without requiring another skill to be installed.

Most repositories need one glossary and `docs/adr/`. Multiple contexts are useful only when the domain already has independent vocabularies and decisions; separate programming languages or runtime directories alone do not justify them.
