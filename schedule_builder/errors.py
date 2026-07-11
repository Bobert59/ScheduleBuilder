class ScheduleBuilderError(Exception):
    """Base class for user-facing scheduler errors."""


class ConfigurationError(ScheduleBuilderError):
    """Raised when a configuration file is invalid."""


class HistoryFormatError(ScheduleBuilderError):
    """Raised when the input workbook is not a valid schedule history."""


class ScheduleInfeasibleError(ScheduleBuilderError):
    """Raised when the optimizer cannot find a feasible schedule."""


class ScheduleCancelledError(ScheduleBuilderError):
    """Raised when the user stops schedule generation."""
