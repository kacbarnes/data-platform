class LakeError(Exception):
    """Expected, user-actionable platform error."""


class ContractError(LakeError):
    """A dataset contract is missing or invalid."""


class QualityError(LakeError):
    """One or more contracted quality checks failed."""

