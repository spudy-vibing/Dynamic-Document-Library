<!-- B1 naive-parallel baseline prompt. version: 1 -->
<!-- Frozen baseline (EVAL-3). Each call sees only its own section, the style guide, the data and -->
<!-- attachments, and the text of the sections it depends on. No outline, no other sections. -->
<!-- Any change: owner review, then bump the version line. Shared fragments: _shared.md. -->

=== user ===
Write one section of a document, using only the data and attachments above as your source of facts.

Audience: {{audience}}
Style: {{style}}

{{section}}
{{dependencies}}
Rules:
- Write only the body of this section, in Markdown. Do not start with the section title; it is added for you.
- Follow the instruction and constraints.
{{fact_rules}}
- Output only the section text.

Write the section titled "{{title}}".

=== dependencies ===

This section builds on the following section(s), which are already written:

{{items}}

=== dependency_item ===
<written_section title="{{title}}">
{{text}}
</written_section>
