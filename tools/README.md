# tools

## `mdpdf.py`

Renders a Markdown subset to PDF: headings, paragraphs, bullet and
numbered lists, pipe tables, bold, inline code, horizontal rules.

Used to produce the review pack for the CTO from `CTO-BRIEF.md` and
`REPORTING-SCOPE.md`. Needs `reportlab` (`pip install reportlab`);
nothing else.

```bash
{ cat CTO-BRIEF.md; echo; echo; \
  sed '1s/^# What goes into the reporting build/# Appendix: detailed scope/' REPORTING-SCOPE.md; } \
  > /tmp/combined.md

python3 tools/mdpdf.py /tmp/combined.md AUS-Reporting-Scope-and-Rationale.pdf \
  "AUS Reporting - scope and rationale - 22 September 2026" \
  --omit "Blocked before any figure is published" \
  --omit "Outstanding, unrelated to scope"
```

`--omit` drops a `## Heading` section and everything under it, up to the
next heading of the same or higher level. It is repeatable, and it fails
loudly if a named section does not exist, so a renamed heading cannot
silently start appearing in a document meant to be shared.

The two omitted above are internal: open questions for the dev team, and
housekeeping items unrelated to the data scope. They stay in
`REPORTING-SCOPE.md` because that is the working record; they just do not
belong in the copy that goes outside.

Table columns are sized by measured text width rather than character
count, because inline code renders in Courier and is wider per character
than the body font. A column is never squeezed below its widest
unbreakable token, so identifiers like `OverflowReporting` do not split
across lines.
