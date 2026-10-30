<!-- P2 prototype prompt: the shared brief (FR-COH-2). version: 1 -->
<!-- Candidate for DocCode's built-in brief instruction (frozen in WP-003 after G0, decision 0006). -->
<!-- "instruction" is the per-prompt part of the _brief call (no position line: it is a helper prompt); -->
<!-- "prefix" wraps the brief in every later prompt's shared prefix. Any change: bump the version line. -->

=== instruction ===
Before any section is written, write a short shared brief for the document described in the frame above. Every section writer will see it. Use only the data and attachments above as your source of facts.

Use exactly these four level-2 headings, in this order, and nothing else:

## Core message
One or two sentences: the message the whole document must leave with the reader.

## Section points
One bullet per section, in outline order, in the form `- <section id>: <the key point this section must make>`. The section IDs are: {{section_ids}}.

## Terms
Preferred terms for the key concepts and figures, and the terms to avoid, as bullets (for example `- "active customers", not "users"`).

## Avoid
Bullets naming material no section should include, such as figures that are not in the data, speculation, or points that belong to one section and must not be repeated in others.

Keep the brief under 300 words. Do not write any section text. Output only the brief.

=== prefix ===
<shared_brief>
{{brief}}
</shared_brief>
Every section follows this brief: make its own section point, use the agreed terms, and leave out what the brief says to avoid.
