"""Compatibility imports for the single shared workbench progress contract."""

from esg_runtime.progress import (
    BusinessProgress,
    ExecutionTiming,
    TaskError,
    STAGE_LABELS,
    IR_BUILD_LABELS,
    stage_label,
    classify_error,
    progress_from_event,
    elapsed_seconds,
)
