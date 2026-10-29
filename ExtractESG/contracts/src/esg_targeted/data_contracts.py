"""Exactly two produced-value contracts, shared by every reader."""
from .data_kinds.quantitative import QuantitativeValue
from .data_kinds.qualitative import QualitativeValue
DATA_CONTRACT_VERSION='extraction-data-2.0'
CONTRACT_BY_KIND={'quantitative':QuantitativeValue,'assertion':QualitativeValue}
