from dataclasses import dataclass, field
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
    archive_id: str = ""
    public_url: str = ""
    post_meta: dict = None      # اطلاعات پست: author, username, date, title
