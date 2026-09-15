---
name: setup-matt-pocock-skills
description: Configure or repair this repository's engineering-skill links, issue tracker, triage labels, and domain-document routing.
---

# Configure engineering skills

Inspect existing `AGENTS.md` / `CLAUDE.md`, `docs/agents/`, the Git remote and installed skills. Reuse established choices and edit only requested or missing configuration. An already configured repository does not need another onboarding interview.

## Choose relevant configuration

- **Issue tracker:** Reuse the configured tracker; otherwise infer a candidate from the remote. Ask only when the user's choice remains ambiguous or changes the existing setup. Read one matching seed: [GitHub](issue-tracker-github.md), [GitLab](issue-tracker-gitlab.md), or [local Markdown](issue-tracker-local.md). Copy only relevant conventions, not optional workflows for uninstalled skills.
- **Triage labels:** Only when triage setup is requested or needed, inspect the tracker labels and existing mapping. Use [triage-labels.md](triage-labels.md) as a seed. Do not duplicate existing labels or create remote labels without authorization.
- **Domain docs:** Use the actual layout and [domain.md](domain.md) as guidance. Multiple runtime directories do not imply multiple business contexts. Preserve existing docs and accepted ADRs; no placeholder glossary or ADR is needed for setup alone.

## Edit and finish

Update the existing instruction entrypoint and links in place. If both `AGENTS.md` and `CLAUDE.md` exist, preserve their established roles rather than copying the same rules into both. If neither exists, use `AGENTS.md` unless the user chose another entrypoint.

Apply authorized local edits directly and leave a reviewable diff; missing preferences do not prevent unrelated repairs. Follow the repository development workflow for shared files. Do not overwrite project-specific rules with a seed template.

Keep existing skill invocation policies. `skills-lock.json` records upstream installation provenance; locally maintained skill contents are authoritative. Reinstalling upstream skills can overwrite these edits and is a separate requested operation.

Verify that links resolve and descriptions match the capabilities they advertise. Report which settings changed and any unresolved choice; unchanged configuration needs no rewrite.
