"""Machine-readable eligibility reasons with backwards-compatible display text."""


class HistoryIssue(str):
    """A display reason whose kind is assigned by its calculation, never parsed."""

    def __new__(cls, message, kind='transaction'):
        value = super().__new__(cls, message)
        value.kind = kind
        return value

    def __getnewargs__(self):
        return str(self), self.kind


def issue_kind(reason):
    return getattr(reason, 'kind', 'transaction')
