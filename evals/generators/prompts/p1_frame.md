<!-- P1 prototype prompt: the document frame (FR-COH-1). version: 1 -->
<!-- Candidate text for DocCode's built-in frame (EVALS_PROPOSAL §4.3): tuned on the dev set only, -->
<!-- frozen with the G0 decision (0006). Any change: bump the version line. Shared fragments: _shared.md. -->
<!-- "frame" is the byte-identical outline frame in the shared prefix; "position" opens the per-prompt part. -->

=== frame ===
<document_frame>
Title: {{title}}
Purpose: {{purpose}}
Audience: {{audience}}
Style: {{style}}

Outline:
{{outline}}

Each section covers only its own scope; do not repeat material that belongs to another section.
</document_frame>

=== outline_item ===
{{index}}. {{title}}: {{brief}}

=== position ===
You are writing section {{index}} of {{count}}, "{{title}}" ({{brief}}).

=== user ===
{{position}}

Write this section of the document described in the frame above, using only the data and attachments above as your source of facts.

{{section}}
{{dependencies}}
Rules:
- Write only the body of this section, in Markdown. Do not start with the section title; it is added for you.
- Follow the instruction and constraints.
- Keep to this section's scope in the outline; the other sections cover the rest.
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
