class StandardPackageError(ValueError):
    """Base error for invalid source or compiled standard packages."""


class PackageIntegrityError(StandardPackageError):
    """Raised when cross-file references or package invariants are invalid."""
