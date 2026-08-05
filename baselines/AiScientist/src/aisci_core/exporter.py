from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from aisci_core.models import JobPaths


def export_job_bundle(job_paths: JobPaths, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as zf:
        for root_name, root_path in (
            ("input", job_paths.input_dir),
            ("workspace", job_paths.workspace_dir),
            ("logs", job_paths.logs_dir),
            ("artifacts", job_paths.artifacts_dir),
            ("state", job_paths.state_dir),
        ):
            if not root_path.exists():
                continue
            for path in sorted(root_path.rglob("*")):
                if path.is_file():
                    zf.write(path, arcname=str(Path(root_name) / path.relative_to(root_path)))
    return output_path

