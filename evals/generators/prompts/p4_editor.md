<!-- P4 prototype prompt: the editor pass (FR-COH-4). version: 1 -->
<!-- Candidate for DocCode's built-in editor prompt (WP-210), tuned on the dev set at G0 (decision 0006). -->
<!-- "user" is the per-prompt part of the _editor call; it follows the same shared prefix as the sections -->
<!-- (frame, data, attachments, brief). Any change: bump the version line. -->

=== user ===
You are now the editor of the whole draft below, not a section writer. The sections were written separately; make small edits so that the document reads as one piece.

Look for, in this order:
1. contradictions between sections (figures, claims or recommendations that disagree);
2. repeated material: the same point or phrase in more than one section (keep it in the section whose scope it belongs to, as the outline in the frame says);
3. inconsistent terms (use the shared brief's terms, if there is a brief);
4. missing transitions, where one short phrase would help the reader move from one section to the next.

Rules for edits:
- Each edit replaces one passage of one section's editable text. `find` must be copied character for character from that section's `<editable>` text and must occur there exactly once. Keep `find` short: a phrase or one sentence.
- Never edit `<read_only>` blocks (data tables and metrics). Do not add figures that are not in the data.
- Change at most {{max_change_pct}}% of any section's text. Do not rewrite sections. No edit is better than a risky one.
- If you see a problem that a small edit cannot fix, describe it in `notes` instead.

{{findings}}

<draft>
{{draft}}
</draft>

Reply with only a JSON object, with no other text and no code fence:
{"edits": [{"section_id": "...", "find": "...", "replace": "...", "reason": "..."}], "notes": ["..."]}

=== findings ===
<repetition_findings>
An automatic check found these pairs of sections sharing word sequences of 8 words or more. Remove the repetition from the section where it does not belong.
{{items}}
</repetition_findings>

=== finding_item ===
- {{a}} and {{b}} share {{count}} 8-word sequence(s), for example "{{phrase}}"

=== no_findings ===
<repetition_findings>
An automatic check found no 8-word sequences shared between sections.
</repetition_findings>

=== draft_section ===
<draft_section id="{{id}}" title="{{title}}">
{{blocks}}
</draft_section>

=== read_only ===
<read_only kind="{{kind}}">
{{text}}
</read_only>

=== editable ===
<editable>
{{text}}
</editable>

=== retry ===
That reply could not be used ({{error}}). Reply again with only the JSON object described above.
