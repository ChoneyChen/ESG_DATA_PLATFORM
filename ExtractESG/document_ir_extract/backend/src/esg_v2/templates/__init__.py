"""Template adapters convert spreadsheets into generic task contracts and back."""

from esg_v2.templates.esrs_trial_xlsx import EsrsTrialXlsxAdapter
from esg_v2.templates.registry import TemplateAdapterRegistry

__all__ = ["EsrsTrialXlsxAdapter", "TemplateAdapterRegistry"]
