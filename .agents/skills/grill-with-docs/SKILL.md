---
name: grill-with-docs
description: Clarify a requested design through focused questions and record accepted terms and architecture decisions.
---

# Clarify a design and record decisions

Read the user's design and only the relevant existing glossary, architecture and contracts. Identify the unresolved choices that would change implementation or acceptance.

Ask focused questions with concrete tradeoffs; reuse answers already given. Continue independent investigation while awaiting input. Stop the interview when the requested design is actionable, not after an arbitrary number of rounds.

Record accepted terms in the existing glossary and accepted architectural decisions in the repository's ADR layout. Separate open proposals from accepted decisions. Small implementation choices do not need new ADRs.

This skill is self-contained: do not require a generic Skill tool or uninstalled `grilling` / `domain-modeling` skills. Follow the repository workflow when changing shared docs. A user who requests discussion only receives a design summary without file or tracker mutations.
