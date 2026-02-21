from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class ArchiveArtifact:
    url: str
    created_at: datetime
    folder: Path
    raw_html_path: Path
    rendered_html_path: Path    # SingleFile-style self-contained HTML
    screenshot_path: Path
    archive_id: str = ""        # UUID stored in DB
    public_url: str = ""        # /view/{archive_id}
