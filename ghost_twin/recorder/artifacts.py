"""Where screenshots and videos live. Only sessions with findings keep their evidence."""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path


class ArtifactManager:
    def __init__(self, data_dir: str | Path, run_id: int):
        self.data_dir = Path(data_dir)
        self.run_dir = self.data_dir / "runs" / str(run_id)
        self.screens = self.run_dir / "screens"
        self.videos = self.run_dir / "videos"
        self.tmp = self.run_dir / "_tmp_video"

    def rel(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.data_dir.resolve()).as_posix()

    def video_tmp_dir(self, session_id: int) -> Path:
        p = self.tmp / f"session-{session_id}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def screenshot_path(self, session_id: int, step_idx: int) -> Path:
        self.screens.mkdir(parents=True, exist_ok=True)
        return self.screens / f"session-{session_id}-step-{step_idx}.png"

    def keep_videos(self, session_id: int, sources: list[Path]) -> list[Path]:
        self.videos.mkdir(parents=True, exist_ok=True)
        kept = []
        for n, src in enumerate(s for s in sources if s and Path(s).exists()):
            suffix = "" if n == 0 else f"-{n + 1}"
            dst = self.videos / f"session-{session_id}{suffix}.webm"
            shutil.move(str(src), dst)
            kept.append(dst)
        return kept

    def discard(self, paths: list[Path]) -> None:
        for p in paths:
            with contextlib.suppress(OSError):
                Path(p).unlink(missing_ok=True)

    def cleanup_tmp(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)
