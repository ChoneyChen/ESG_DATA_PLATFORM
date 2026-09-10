"""Portable ESG standard-package contracts and compiler."""

__version__ = "0.2.0"

from .compiler import StandardPackageCompiler
from .contracts import CompiledStandardPackage, CoreSchemaPackage, ModuleSource

__all__ = [
    "CompiledStandardPackage",
    "CoreSchemaPackage",
    "ModuleSource",
    "StandardPackageCompiler",
]
