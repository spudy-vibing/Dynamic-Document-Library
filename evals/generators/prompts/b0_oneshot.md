<!-- B0 one-shot baseline prompt. version: 1 -->
<!-- Frozen baseline (EVAL-3): "what a competent developer writes in an afternoon". -->
<!-- Any change: owner review, then bump the version line. Shared fragments: _shared.md. -->

=== user ===
Write the document described below, using only the data and attachments above as your source of facts.

{{document}}

<outline>
{{outline}}
</outline>

Rules:
- Write every section in the outline, in order. Start each section with a level-2 heading that is exactly its title, for example `## {{first_title}}`. Do not add a document title or any other level-1 or level-2 headings.
- Follow each section's instruction and constraints.
{{fact_rules}}
- Output only the document.
