"""How each affiliate's leads reach us, from Layton's notes of 15 Oct 2026.

The journey decides which metrics mean anything. An affiliate that sends
bank statements with the lead skips the extraction step entirely, so its
leads-to-statement rate is not low - it does not exist, and charting it
as zero would raise an alarm about nothing.
"""

from __future__ import annotations

IMMEDIATE_WITH_STATEMENTS = "immediate_with_statements"
IMMEDIATE_WITHOUT_STATEMENTS = "immediate_without_statements"
OFFLINE_REFERRAL = "offline_referral"
INTERNAL = "internal"
UNKNOWN = "unknown"

LABELS = {
    IMMEDIATE_WITH_STATEMENTS: "Immediate, statements supplied",
    IMMEDIATE_WITHOUT_STATEMENTS: "Immediate, we retrieve statements",
    OFFLINE_REFERRAL: "Offline referral",
    INTERNAL: "Internal journey",
    UNKNOWN: "Not yet described",
}

DESCRIPTIONS = {
    IMMEDIATE_WITH_STATEMENTS:
        "Posts straight into our API with bank statements already retrieved, so the "
        "lead goes directly into the pingtree with no bank statement cost.",
    IMMEDIATE_WITHOUT_STATEMENTS:
        "Posts immediately but without statements; we retrieve them when the customer "
        "reaches us, and the cost is passed on to the affiliate.",
    OFFLINE_REFERRAL:
        "Posts the lead offline; we remarket by email and SMS to bring the customer "
        "back into our journey. A large drop to statement is normal - many are no "
        "longer interested.",
    INTERNAL:
        "Our own journey: the customer submits an application, then retrieves bank "
        "statements through Talefin before the lead is processed.",
    UNKNOWN: "",
}

BY_AFFILIATE = {
    63: IMMEDIATE_WITH_STATEMENTS,       # JRF1st
    59: IMMEDIATE_WITH_STATEMENTS,       # Borrowly
    189: IMMEDIATE_WITH_STATEMENTS,      # MonziIFrame
    26: OFFLINE_REFERRAL,                # MoneySpot
    117: IMMEDIATE_WITHOUT_STATEMENTS,   # Smart Lead Solutions
    1: IMMEDIATE_WITHOUT_STATEMENTS,     # MoneyMe
    28: IMMEDIATE_WITHOUT_STATEMENTS,    # CashPal
    32: IMMEDIATE_WITHOUT_STATEMENTS,    # Breezy Loans
    105: IMMEDIATE_WITHOUT_STATEMENTS,   # Loans Buddy
    161: IMMEDIATE_WITHOUT_STATEMENTS,   # Breezy 4th
}


def journey_of(affiliate_id, source):
    if source == "internal":
        return INTERNAL
    return BY_AFFILIATE.get(affiliate_id, UNKNOWN)


def has_statement_step(journey):
    """Whether leads-to-statement is a rate at all for this journey."""
    return journey != IMMEDIATE_WITH_STATEMENTS
