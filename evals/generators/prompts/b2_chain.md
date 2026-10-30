<!-- B2 sequential chat-chain baseline prompt. version: 1 -->
<!-- Frozen baseline (EVAL-3): the "paste into chat" workflow. One append-only conversation, -->
<!-- one user turn per section in document order. Any change: owner review, then bump the version. -->

=== first_turn ===
I need your help writing a document, one section at a time. Use only the data and attachments above as your source of facts.

{{document}}

<outline>
{{outline}}
</outline>

Rules:
- I will ask for one section per message. Each time, write only the body of that section in Markdown. Do not start with the section title; it is added for you.
- Follow each section's instruction and constraints.
{{fact_rules}}
- Output only the section text.

Write section 1 of {{count}}, the section titled "{{title}}".

=== next_turn ===
Next, write section {{index}} of {{count}}, the section titled "{{title}}".
