"""Exceptions raised by pytoolbox tools."""


class PytoolboxError(Exception):
    """Base class for failures a tool reports to its caller."""


class InputError(PytoolboxError):
    """A bad argument or a missing path. The CLI exits with 1."""


class OperationError(PytoolboxError):
    """A failure while doing the work. The CLI exits with 2."""
