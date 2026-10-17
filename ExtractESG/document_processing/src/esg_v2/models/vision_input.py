from __future__ import annotations

import base64
import io
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image


class QiniuVisionInputResolver:
    """Prefers cloud-addressable artifacts; local data URIs are a dev fallback."""

    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

    def resolve(self, refs: list[str], *, limit: int = 3) -> list[str]:
        remote = [ref for ref in refs if ref.startswith(("http://", "https://", "kodo://"))]
        if remote:
            return list(dict.fromkeys(remote))[:limit]
        local = []
        for ref in refs:
            path = Path(ref)
            if path.exists() and path.suffix.lower() in self.IMAGE_SUFFIXES:
                local.append(self._data_uri(path))
        return list(dict.fromkeys(local))[:limit]

    @staticmethod
    def _data_uri(path: Path) -> str:
        try:
            with Image.open(path) as image:
                image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                if image.mode in {"RGBA", "LA"}:
                    background = Image.new("RGB", image.size, "white")
                    background.paste(image, mask=image.getchannel("A"))
                    image = background
                elif image.mode != "RGB":
                    image = image.convert("RGB")
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=84, optimize=True)
                encoded = base64.b64encode(output.getvalue()).decode("ascii")
                return f"data:image/jpeg;base64,{encoded}"
        except (OSError, ValueError):
            mime = mimetypes.guess_type(str(path))[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{encoded}"


class LocalVisionInputResolver:
    """Keeps local visual artifacts as paths for the MLX worker."""

    IMAGE_SUFFIXES = QiniuVisionInputResolver.IMAGE_SUFFIXES

    def resolve(self, refs: list[str], *, limit: int = 3) -> list[str]:
        images: list[str] = []
        for ref in refs:
            value = ref
            if ref.startswith("file://"):
                value = unquote(urlparse(ref).path)
            path = Path(value).expanduser()
            if path.exists() and path.suffix.lower() in self.IMAGE_SUFFIXES:
                images.append(str(path.resolve()))
                continue
            if ref.startswith(("http://", "https://")):
                images.append(ref)
        return list(dict.fromkeys(images))[:limit]
