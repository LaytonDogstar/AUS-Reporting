import unittest

from aus_reporting.sensitive import (
    BlockedColumnError,
    UnapprovedRestrictedColumnError,
    check_columns,
    classify,
    normalise,
)


class TestNormalise(unittest.TestCase):
    def test_folds_case_underscores_and_spaces(self):
        for variant in ("AccountNumber", "account_number", "ACCOUNT NUMBER", "accountnumber"):
            self.assertEqual(normalise(variant), "accountnumber")


class TestClassify(unittest.TestCase):
    def test_banking_and_secrets_are_blocked(self):
        for column in ("AccountNumber", "SortCode", "Password",
                       "CredfinSecretKey", "TalefinClientSecret", "cvv"):
            self.assertEqual(classify(column), "blocked", column)

    def test_personal_identifiers_are_blocked(self):
        for column in ("FirstName", "Email", "MobileNumber", "DateOfBirth",
                       "DriversLicense", "ContactName", "AccountName", "Employer"):
            self.assertEqual(classify(column), "blocked", column)

    def test_online_identifiers_are_restricted(self):
        # A device or browser, not a person, and occasionally defensible.
        for column in ("CookieId", "fbp", "gclid", "UserAgent", "IpAddress"):
            self.assertEqual(classify(column), "restricted", column)

    def test_geography_is_open(self):
        # Deliberate. Reporting needs geography and none of these
        # identifies a person alone. Re-identification in combination
        # with age and income is handled by banding and small-cell
        # suppression at presentation, not by refusing to extract.
        for column in ("PostCode", "City", "StateCode"):
            self.assertEqual(classify(column), "open", column)

    def test_ordinary_columns_are_open(self):
        for column in ("Id", "DateCreated", "StateCode", "LoanAmount", "AffiliateId"):
            self.assertEqual(classify(column), "open", column)


class TestCheckColumns(unittest.TestCase):
    def test_passes_open_columns(self):
        check_columns(("Id", "DateCreated", "StateCode"))  # no raise

    def test_blocked_column_raises(self):
        with self.assertRaises(BlockedColumnError) as ctx:
            check_columns(("Id", "AccountNumber"))
        self.assertIn("AccountNumber", str(ctx.exception))

    def test_blocked_column_cannot_be_approved(self):
        # The whole point: there is no override for banking or secrets.
        with self.assertRaises(BlockedColumnError):
            check_columns(("Id", "SortCode"), approved=("SortCode",))

    def test_restricted_column_needs_approval(self):
        with self.assertRaises(UnapprovedRestrictedColumnError):
            check_columns(("Id", "CookieId"))

    def test_restricted_column_passes_when_approved(self):
        check_columns(("Id", "CookieId"), approved=("CookieId",))  # no raise

    def test_approval_is_case_insensitive(self):
        check_columns(("Id", "UserAgent"), approved=("user_agent",))

    def test_personal_identifier_cannot_be_approved(self):
        # The point of the change: approving a name or an email is not
        # something the configuration can express any more.
        for column in ("Email", "FirstName", "DateOfBirth", "MobileNumber"):
            with self.assertRaises(BlockedColumnError):
                check_columns(("Id", column), approved=(column,))

    def test_error_names_the_table(self):
        with self.assertRaises(BlockedColumnError) as ctx:
            check_columns(("Password",), where="Overflow.dbo.Affiliates")
        self.assertIn("Overflow.dbo.Affiliates", str(ctx.exception))

    def test_real_affiliates_secrets_are_caught(self):
        # These three actually exist in Overflow.Affiliates.
        for column in ("Password", "CredfinSecretKey", "TalefinClientSecret"):
            with self.assertRaises(BlockedColumnError, msg=column):
                check_columns(("Id", column))


if __name__ == "__main__":
    unittest.main()
