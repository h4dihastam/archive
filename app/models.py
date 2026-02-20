from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class ArchiveArtifact:
    url: str
    created_at: datetime
    folder: Path
    raw_html_path: Path
    rendered_html_path: Path
    screenshot_path: Path
