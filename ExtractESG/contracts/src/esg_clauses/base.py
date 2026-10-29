from __future__ import annotations
import hashlib
import json
from pydantic import BaseModel, ConfigDict

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

from esg_data.hashing import digest
