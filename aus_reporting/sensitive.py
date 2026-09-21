"""Column-level guards on what may leave the source databases.

Two tiers, deliberately:

* ``BLOCKED`` - banking details and credentials. There is no reporting
  question that needs them and no configuration option to override.
  ``Overflow.Affiliates`` alone holds ``Password``, ``CredfinSecretKey``
  and ``TalefinClientSecret``; ``BankStatementSummaries`` holds
  ``AccountNumber`` and ``SortCode``. None of it is extracted.

* ``RESTRICTED`` - direct personal identifiers. Legitimately needed
  occasionally, so allowed, but only when the table spec names the
  column in ``restricted_approved``. That makes every instance a visible,
  reviewable line in ``tables.yml`` rather than an accident.

Matching is on a normalised column name (lowercased, underscores
stripped), so ``Account_Number``, ``accountnumber`` and ``AccountNumber``
are all caught.
"""

from __future__ import annotations

from .errors import BlockedColumnError, UnapprovedRestrictedColumnError

BLOCKED: frozenset[str] = frozenset({
    # Bank account detail
    "accountnumber", "accountno", "bankaccount", "bankaccountnumber",
    "sortcode", "bsb", "iban", "swift",
    # Card detail
    "cardnumber", "cardnum", "cvv", "cvc",
    # Secrets
    "password", "passwd", "secret", "secretkey", "clientsecret",
    "apikey", "token", "accesstoken", "refreshtoken",
    "credfinsecretkey", "talefinclientsecret", "passwordsalt",
})

RESTRICTED: frozenset[str] = frozenset({
    "firstname", "lastname", "surname", "fullname", "middlename",
    "contactname",
    "email", "emailaddress",
    "mobile", "mobilenumber", "phone", "phonenumber", "landlinenumber",
    "dateofbirth", "dob",
    "driverslicense", "driverslicence", "passport", "medicare", "tfn",
    "street", "streetnumber", "unitnumber", "addressline1", "addressline2",
    "ipaddress",
})


__all__ = [
    "BLOCKED", "RESTRICTED", "BlockedColumnError",
    "UnapprovedRestrictedColumnError", "check_columns", "classify", "normalise",
]


def normalise(column: str) -> str:
    """Fold a column name for comparison against the tiers."""
    return column.replace("_", "").replace(" ", "").lower()


def classify(column: str) -> str:
    """Return ``"blocked"``, ``"restricted"`` or ``"open"``."""
    key = normalise(column)
    if key in BLOCKED:
        return "blocked"
    if key in RESTRICTED:
        return "restricted"
    return "open"


def check_columns(
    columns: tuple[str, ...] | list[str],
    *,
    approved: tuple[str, ...] | list[str] = (),
    where: str = "<unknown>",
) -> None:
    """Raise if ``columns`` contains anything it should not.

    ``approved`` lists restricted columns the spec has explicitly
    accepted. Blocked columns are never permitted, whatever is approved.
    """
    approved_keys = {normalise(c) for c in approved}

    blocked = [c for c in columns if classify(c) == "blocked"]
    if blocked:
        raise BlockedColumnError(
            f"{where}: these columns may never be extracted: "
            f"{', '.join(sorted(blocked))}. They are banking details or "
            f"credentials; there is no override."
        )

    unapproved = [
        c for c in columns
        if classify(c) == "restricted" and normalise(c) not in approved_keys
    ]
    if unapproved:
        raise UnapprovedRestrictedColumnError(
            f"{where}: these columns are personal identifiers and need to be "
            f"listed under restricted_approved, with a reason, before they can "
            f"be extracted: {', '.join(sorted(unapproved))}."
        )
