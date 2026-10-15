"""Whether conversion is holding up over time, per affiliate and per
lender.

Per affiliate, the three measures Layton asked to watch:

    leads to statement   statements extracted / leads received
    submitted to sold    sold / submitted
    earnings per lead    revenue / leads received

Per lender, from the lender report:

    filter pass          sent / processed     how many leads get through
                                              the lender's filters
    acceptance           accepted / sent      how many of those it buys
    redirect rate        redirect / accepted  how many sales reach the
                                              lender's own page

Daily rates on a small affiliate are mostly noise - ten leads and one
sale is 10%, ten and none is 0% - so every point is a rolling seven
days, and a drop is only called when it is both large and more than
chance would produce.
"""

from __future__ import annotations

import math

WINDOW = 7                  # days in each rolling point, and in "recent"
MIN_RECENT = 50             # leads (or submissions) in the recent week to judge it
REL_DROP = 0.10             # a rate must fall at least this much, relatively
Z_DROP = -2.0               # and by more than chance: two standard errors
EPL_REL_DROP = 0.15         # earnings per lead has no simple test, so a wider margin

MEASURES = {
    # name: (numerator field, denominator field)
    "leads_to_statement": ("extracted", "received"),
    "submitted_to_sold": ("sold", "submitted"),
    "earnings_per_lead": ("revenue", "received"),
}

LENDER_MEASURES = {
    "filter_pass": ("sent", "processed"),
    "acceptance": ("accepted", "sent"),
    "redirect_rate": ("redirect", "accepted"),
}

# Measures that are an amount per lead rather than a proportion, so no
# two-proportion test applies and a wider margin stands in for one.
AMOUNTS = ("earnings_per_lead",)


def rolling(days, num, den, window=WINDOW):
    """[(day, rate)] where each rate covers the `window` days ending that day.

    Summed then divided, never an average of daily rates: a day with
    three leads must not weigh as much as a day with three hundred.
    """
    out = []
    for i in range(window - 1, len(days)):
        span = days[i - window + 1:i + 1]
        n = sum(d[num] or 0 for d in span)
        d_ = sum(d[den] or 0 for d in span)
        out.append((days[i]["day"], (n / d_) if d_ else None))
    return out


def rolling_mean(days, field, window=WINDOW):
    """[(day, per-day average)] over the `window` days ending that day -
    a volume trend that a quiet Sunday does not throw about."""
    out = []
    for i in range(window - 1, len(days)):
        span = days[i - window + 1:i + 1]
        out.append((days[i]["day"], sum(d[field] or 0 for d in span) / window))
    return out


# What a lender does with one source's leads.
PAIR_MEASURES = {"acceptance": ("sold", "passed")}


def assess(days, measure, window=WINDOW, measures=MEASURES):
    """The latest week against everything before it in the window.

    Returns current, baseline, relative change, and whether it is a drop
    worth looking at. For a rate, a drop needs both a relative fall of
    at least REL_DROP and a two-proportion z below Z_DROP; for earnings
    per lead, a fall of EPL_REL_DROP. Too little volume returns no call
    rather than a guess.
    """
    num, den = measures[measure]
    recent, before = days[-window:], days[:-window]
    rn = sum(d[num] or 0 for d in recent)
    rd = sum(d[den] or 0 for d in recent)
    bn = sum(d[num] or 0 for d in before)
    bd = sum(d[den] or 0 for d in before)
    current = (rn / rd) if rd else None
    baseline = (bn / bd) if bd else None
    result = {"current": current, "baseline": baseline, "change": None,
              "flag": False, "enough": rd >= MIN_RECENT and bd >= MIN_RECENT}
    if current is None or not baseline:
        return result
    result["change"] = (current - baseline) / baseline
    if not result["enough"]:
        return result

    if measure in AMOUNTS:
        result["flag"] = result["change"] <= -EPL_REL_DROP
        return result

    pooled = (rn + bn) / (rd + bd)
    se = math.sqrt(pooled * (1 - pooled) * (1 / rd + 1 / bd)) if 0 < pooled < 1 else 0
    z = (current - baseline) / se if se else 0
    result["z"] = z
    result["flag"] = result["change"] <= -REL_DROP and z <= Z_DROP
    return result


def assess_shift(days, num, den, window=WINDOW):
    """Has a share moved, either way? The latest week against the weeks
    before it, for a band's share of what a lender bought.

    A drop in conversion is bad news; a shift in who a lender buys is
    neither good nor bad, so both directions are called, on the same
    terms as a drop: at least REL_DROP relative change and a
    two-proportion z beyond Z_DROP either side.
    """
    recent, before = days[-window:], days[:-window]
    rn = sum(d[num] or 0 for d in recent)
    rd = sum(d[den] or 0 for d in recent)
    bn = sum(d[num] or 0 for d in before)
    bd = sum(d[den] or 0 for d in before)
    current = (rn / rd) if rd else None
    baseline = (bn / bd) if bd else None
    out = {"current": current, "baseline": baseline, "change": None, "shift": 0,
           "enough": rd >= MIN_RECENT and bd >= MIN_RECENT}
    if current is None or not baseline:
        return out
    out["change"] = (current - baseline) / baseline
    if not out["enough"]:
        return out
    pooled = (rn + bn) / (rd + bd)
    se = math.sqrt(pooled * (1 - pooled) * (1 / rd + 1 / bd)) if 0 < pooled < 1 else 0
    z = (current - baseline) / se if se else 0
    out["z"] = z
    if abs(out["change"]) >= REL_DROP and abs(z) >= -Z_DROP:
        out["shift"] = 1 if z > 0 else -1
    return out
