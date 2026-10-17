"""Compatibility entrypoint; semantic authoring lives under standards/authoring."""
from esg_standard_packages.build import main, export_authoring

def generate(destination):
    return export_authoring(destination)

if __name__ == "__main__":
    raise SystemExit(main())
