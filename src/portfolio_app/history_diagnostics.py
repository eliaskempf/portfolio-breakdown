"""Machine-readable eligibility reasons with backwards-compatible display text."""


class HistoryIssue(str):
    """A display reason whose kind is assigned by its calculation, never parsed."""

    def __new__(cls, message, kind='transaction', causes=()):
        value = super().__new__(cls, message)
        value.kind = kind
        value.causes = tuple(causes)
        return value

    def __getnewargs__(self):
        return str(self), self.kind, self.causes


def issue_kind(reason):
    return getattr(reason, 'kind', 'transaction')


def issue_tree(reasons):
    """Yield wrappers and their original typed evidence requirements."""
    for reason in reasons:
        yield reason
        yield from issue_tree(getattr(reason, 'causes', ()))
