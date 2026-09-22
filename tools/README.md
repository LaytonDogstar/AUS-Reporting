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
  "AUS Reporting - scope and rationale - 22 September 2026"
```

Table columns are sized by measured text width rather than character
count, because inline code renders in Courier and is wider per character
than the body font. A column is never squeezed below its widest
unbreakable token, so identifiers like `OverflowReporting` do not split
across lines.
