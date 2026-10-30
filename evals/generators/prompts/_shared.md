<!-- Shared prompt fragments for the baselines B0, B1 and B2. version: 1 -->
<!-- Frozen with the baselines (EVAL-3). Any change: owner review, then bump the version line. -->
<!-- Parts are separated by "=== name ===" lines; {{placeholders}} are filled by generators/common.py. -->

=== system ===
You are an experienced writer of business and technical documents. You write clear, accurate Markdown for busy readers.

=== context ===
The data and attachments for this document follow. They are the only source of facts.

<data>
{{data_json}}
</data>
{{attachments}}

=== attachment ===
<attachment name="{{name}}">
{{text}}
</attachment>

=== document ===
<document>
Title: {{title}}
Purpose: {{purpose}}
Audience: {{audience}}
Style: {{style}}
</document>

=== section_entry ===
<section id="{{id}}" title="{{title}}">
Brief: {{brief}}
Instruction: {{instruction}}{{constraints}}{{data_blocks}}
</section>

=== fact_rules ===
- Use only figures that appear in the data or attachments. Do not invent or estimate numbers.
- Some sections list data blocks (tables or metrics) that are inserted automatically at the start of the section, before your text. Do not reproduce them; refer to them in prose where useful.
