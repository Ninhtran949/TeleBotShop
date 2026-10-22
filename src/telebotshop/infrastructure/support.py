import re
from pathlib import Path

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.support import PublicTerms


class TermsConfigurationError(Exception):
    pass


def load_terms(path: Path | None) -> PublicTerms | None:
    if path is None:
        return None
    try:
        if str(path).startswith("\\\\") or "://" in str(path):
            raise ValueError
        path = path.resolve(strict=True)
        if (
            str(path).startswith("\\\\")
            or "://" in str(path)
            or path.suffix.lower() not in {".md", ".txt"}
        ):
            raise ValueError
        with path.open("rb") as source:
            data = source.read(24001)
        if len(data) > 24000:
            raise ValueError
        text = data.decode("utf-8-sig").replace("\r\n", "\n")
        for match in re.finditer(r"\[([^\]\n]{1,200})\](?!\()", text):
            if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", match[1]):
                raise ValueError
        return PublicTerms(text)
    except (OSError, ValueError, InvalidInput):
        raise TermsConfigurationError("Public terms unavailable or incomplete") from None
