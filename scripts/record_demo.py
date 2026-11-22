#!/usr/bin/env python3
"""Backup-видео демонстрации: реальный прогон scripts/demo.py, отрендеренный в mp4.

Это не постановочная запись: скрипт запускает demo.py против живого API, берёт его настоящий
вывод (числа из API) и отрисовывает кадры терминального вида, затем кодирует их в H.264.

    python3 scripts/record_demo.py                 # docs/demo-backup.mp4
    API_URL=http://host:8000 python3 scripts/record_demo.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT = "/usr/share/fonts/TTF/DejaVuSansMono.ttf"
W, H = 1280, 720
PAD = 28
LINE_H = 22
BG = (11, 16, 26)
FG = (232, 237, 247)
MUTED = (147, 160, 184)
ACCENT = (79, 140, 255)
WARN = (240, 180, 41)
GOOD = (52, 195, 143)


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size)


def color_for(line: str) -> tuple[int, int, int]:
    if line.startswith(("СЦЕНАРИЙ", "ГОТОВО")) or set(line.strip()) == {"="}:
        return ACCENT if line.strip().startswith("СЦЕНАРИЙ") else WARN
    if "ИТОГО" in line:
        return GOOD
    if line.strip().startswith(("Заказ", "Маршрут", "Покупатель", "Начислено", "Предложение", "Перемещено")):
        return FG
    return MUTED if not line.strip() else FG


def render(lines: list[str], index: int, total: int) -> Image.Image:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, W, 44], fill=(23, 29, 43))
    draw.text((PAD, 12), "mesh-pvz · backup-демонстрация · реальный прогон API", font=font(15), fill=MUTED)
    progress = int((W - 2 * PAD) * (index / max(1, total - 1)))
    draw.rectangle([PAD, 44, PAD + progress, 46], fill=ACCENT)

    body_font = font(15)
    visible = lines[: index + 1]
    max_lines = (H - 70 - PAD) // LINE_H
    visible = visible[-max_lines:]
    y = 70
    for line in visible:
        draw.text((PAD, y), line[:118], font=body_font, fill=color_for(line))
        y += LINE_H
    return image


def main() -> int:
    env = dict(os.environ)
    print("запускаю demo.py против живого API…", file=sys.stderr)
    run = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo.py")],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    if run.returncode != 0:
        print(run.stdout[-2000:], run.stderr[-2000:], sep="\n", file=sys.stderr)
        return 1

    lines = run.stdout.rstrip("\n").split("\n")
    if len(lines) < 10:
        print("demo.py вернул слишком мало строк — проверьте API", file=sys.stderr)
        return 1

    work = Path(tempfile.mkdtemp(prefix="demo-video-", dir=os.environ.get("TMPDIR", "/tmp")))
    per_line = 3  # кадров на строку при 10 fps → 0.3 c на строку
    fps = 10
    frame_no = 0
    for index in range(len(lines)):
        for _ in range(per_line):
            render(lines, index, len(lines)).save(work / f"f{frame_no:05d}.png")
            frame_no += 1
    # финальный кадр держим дольше
    for _ in range(fps * 3):
        render(lines, len(lines) - 1, len(lines)).save(work / f"f{frame_no:05d}.png")
        frame_no += 1

    target = ROOT / "docs" / "demo-backup.mp4"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", str(fps),
        "-i", str(work / "f%05d.png"),
        "-vf", "format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "22",
        str(target),
    ]
    subprocess.run(cmd, check=True)
    shutil.rmtree(work, ignore_errors=True)
    size = target.stat().st_size
    duration = frame_no / fps
    print(f"{target} — {size / 1024:.0f} КБ, {duration:.1f} с, строк вывода: {len(lines)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
