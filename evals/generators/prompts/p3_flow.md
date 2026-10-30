<!-- P3 prototype prompt: sequential flow, "the document so far" (FR-COH-3). version: 1 -->
<!-- The header and one block per earlier section are appended to the shared prefix in document order, -->
<!-- so each request's prefix extends the previous one's. Any change: bump the version line. -->

=== header ===
The document so far: the sections already written, in document order, one <written_section> each. Continue from them: do not restate what they say, and refer back to them where it helps the reader.

=== written_section ===
<written_section id="{{id}}" title="{{title}}">
{{text}}
</written_section>

=== cut ===
{{first_paragraph}}

[Rest of this section omitted for length.]

=== continue ===

The sections before this one are in the document so far above; this section continues from them.
