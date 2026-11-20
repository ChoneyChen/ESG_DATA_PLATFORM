"""Write-only operator configuration, resolved by the extraction worker."""

import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile

from esg_contracts.model_providers import CLOUD_MODELS, CloudModelProvider, ModelProviders, ModelProviderStatus


MAX_KEY_LENGTH = 512


class ModelCredentialStore:
    def __init__(self, run_store, provider: CloudModelProvider):
        if provider not in CLOUD_MODELS:
            raise ValueError("Unknown cloud model provider")
        self.directory = run_store.path.parent / "model-credentials"
        self.path = self.directory / provider

    @staticmethod
    def _private(metadata, check_type, permissions):
        if not (
            check_type(metadata.st_mode)
            and metadata.st_uid == os.geteuid()
            and stat.S_IMODE(metadata.st_mode) == permissions
        ):
            raise OSError("The model credential store is not private")

    def _checked_path(self):
        self._private(self.directory.lstat(), stat.S_ISDIR, 0o700)
        self._private(self.path.lstat(), stat.S_ISREG, 0o600)

    def configured(self):
        try:
            self._checked_path()
        except FileNotFoundError:
            return False
        return True

    def read(self):
        try:
            self._checked_path()
            with os.fdopen(os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW), encoding="ascii") as credential:
                self._private(os.fstat(credential.fileno()), stat.S_ISREG, 0o600)
                value = credential.read(MAX_KEY_LENGTH + 1)
        except FileNotFoundError:
            return None
        except UnicodeError:
            raise OSError("The model credential store cannot be read") from None
        if not _valid_key(value):
            raise OSError("The model credential store cannot be read")
        return value

    def write(self, value):
        if not _valid_key(value):
            raise ValueError("Invalid API key format")
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self._private(self.directory.lstat(), stat.S_ISDIR, 0o700)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="ascii", dir=self.directory, delete=False) as output:
                temporary = Path(output.name)
                output.write(value)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def configured_providers(run_store):
    return ModelProviders(providers=[
        ModelProviderStatus(provider="local", configured=True, models=[]),
        *(ModelProviderStatus(provider=provider, configured=ModelCredentialStore(run_store, provider).configured(),
                              models=list(models)) for provider, models in CLOUD_MODELS.items()),
    ])


def _valid_key(value):
    return 0 < len(value) <= MAX_KEY_LENGTH and all("!" <= character <= "~" for character in value)
