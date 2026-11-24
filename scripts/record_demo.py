#!/usr/bin/env python3
"""Резервное видеодемо: реальный прогон UI в Chromium (Playwright) → docs/demo-backup.mp4.

Запись идёт по docs/VIDEO_DEMO_SCRIPT.md, окно 1440×900, роль API — «Маркетплейс»:

  кадр 1 — кабинет продавца, городской заказ, «Заказы», «Собрать» → «Выдать» → «Начисления»
           (четыре стороны и сходимость ledger);
  кадр 2 — «Экономика»: блок «Зелёная зона 4 сторон» и ползунок «Бонус ПВЗ за заказ без хаба»
           15 → 5 ₽ с пересчётом через API;
  кадр 3 — «Демо-кокпит»: шаг «4. Ошибка в логистике / отказ узла», бейдж PASS и «Лог различий».

Стенд должен быть на чистой БД (иначе шаг 4 покажет нули): docker compose down -v && up -d.
Нужны playwright + chromium: .venv/bin/pip install playwright && .venv/bin/python -m playwright install chromium.

    python3 scripts/record_demo.py                       # docs/demo-backup.mp4
    WEB_URL=http://localhost:5173 python3 scripts/record_demo.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB_URL = os.environ.get("WEB_URL", "http://localhost:5173/").rstrip("/") + "/"
API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
OUT = Path(os.environ.get("OUT", ROOT / "docs" / "demo-backup.mp4"))
W, H = 1440, 900
FPS = 25
CRF = "24"
MIN_S, MAX_S = 60, 180
TARGET_S = 118  # добиваем паузы до этого хронометража

CURSOR_JS = """
(() => {
  if (document.getElementById('demo-cursor')) return;
  const c = document.createElement('div');
  c.id = 'demo-cursor';
  c.style.cssText = 'position:fixed;left:-40px;top:-40px;width:16px;height:16px;border-radius:50%;'
    + 'background:rgba(79,140,255,.35);border:2px solid #4f8cff;box-shadow:0 0 0 4px rgba(79,140,255,.15);'
    + 'pointer-events:none;z-index:2147483647;transition:transform .06s linear';
  document.body.appendChild(c);
  document.addEventListener('mousemove', e => {
    c.style.transform = `translate(${e.clientX - 8}px, ${e.clientY - 8}px)`;
  }, true);
  document.addEventListener('mousedown', () => { c.style.background = 'rgba(79,140,255,.75)'; }, true);
  document.addEventListener('mouseup', () => { c.style.background = 'rgba(79,140,255,.35)'; }, true);
  const cap = document.createElement('div');
  cap.id = 'demo-caption';
  cap.style.cssText = 'position:fixed;right:16px;bottom:14px;max-width:640px;padding:7px 12px;border-radius:8px;'
    + 'background:rgba(11,16,26,.86);color:#e8edf7;font:13px/1.35 system-ui,sans-serif;z-index:2147483646;'
    + 'pointer-events:none;opacity:0;transition:opacity .35s';
  document.body.appendChild(cap);
})()
"""


def caption(page, text: str) -> None:
    page.evaluate(
        """(t) => { const c = document.getElementById('demo-caption');
                    if (!c) return; c.textContent = t; c.style.opacity = t ? '1' : '0'; }""",
        text,
    )


def hold(page, seconds: float) -> None:
    page.wait_for_timeout(int(seconds * 1000))


def click(page, selector: str, settle: float = 0.7) -> None:
    """Плавно подвести курсор к элементу и кликнуть — так видно, куда именно нажали."""
    box = page.locator(selector).first.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=14)
        hold(page, 0.35)
    page.locator(selector).first.click()
    hold(page, settle)


def goto_page(page, name: str, title: str, settle: float = 1.6) -> None:
    page.evaluate(CURSOR_JS)  # на случай, если SPA перерисовала body
    caption(page, title)
    click(page, f'button[data-page="{name}"]', settle)


def smooth_scroll(page, selector: str, block: str = "center") -> None:
    page.locator(selector).first.evaluate(
        "(el, block) => el.scrollIntoView({behavior: 'smooth', block})", block
    )
    hold(page, 1.0)


def preflight() -> dict:
    with urllib.request.urlopen(f"{API_URL}/api/v1/health/deep", timeout=10) as resp:
        health = json.load(resp)
    if health.get("status") != "ok":
        raise SystemExit(f"стенд нездоров: {health}")
    if health["counts"]["orders"]:
        raise SystemExit(
            f"в БД уже {health['counts']['orders']} заказов — для записи нужна чистая база: "
            "docker compose down -v && docker compose up -d"
        )
    return health


def record(work: Path) -> Path:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--force-device-scale-factor=1", "--hide-scrollbars"])
        context = browser.new_context(
            viewport={"width": W, "height": H},
            record_video_dir=str(work),
            record_video_size={"width": W, "height": H},
            locale="ru-RU",
        )
        page = context.new_page()
        page.goto(WEB_URL, wait_until="networkidle")
        page.evaluate(CURSOR_JS)

        # роль API — «Маркетплейс» (для скоринга и отказа узла)
        page.select_option("#role-select", "admin")
        if page.input_value("#role-select") != "admin":
            raise SystemExit("не удалось выставить роль API «Маркетплейс»")

        started = time.monotonic()

        # ---------- Кадр 1. Сквозной городской заказ ----------
        caption(page, "Кадр 1 · Кабинет продавца → городской заказ")
        goto_page(page, "seller", "Кадр 1 · Кабинет продавца → городской заказ", 2.5)
        page.mouse.move(700, 420, steps=10)
        hold(page, 2.5)

        # городской заказ: хранение на YOL-01, выдача на YOL-02 (внутри Йошкар-Олы)
        click(page, 'select[name="to_pvz_id"]', 0.4)
        page.select_option('select[name="to_pvz_id"]', label="YOL-02 · ПВЗ Дубки")
        hold(page, 2.0)
        caption(page, "Кадр 1 · Оформляем заказ: YOL-01 → YOL-02, маршрут mesh")
        submit = page.locator('button:has-text("Оформить")').first
        sbox = submit.bounding_box()
        page.mouse.move(sbox["x"] + sbox["width"] / 2, sbox["y"] + sbox["height"] / 2, steps=14)
        hold(page, 0.4)
        with page.expect_response(
            lambda r: r.url.endswith("/api/v1/orders") and r.request.method == "POST", timeout=30_000
        ) as created:
            submit.click()
        if created.value.status not in (200, 201):
            raise SystemExit(f"заказ не создан: {created.value.status} {created.value.text()[:200]}")
        hold(page, 3.0)

        goto_page(page, "orders", "Кадр 1 · Заказы: маршрут mesh 90 ₽ / 3 ч, сборка и выдача")
        page.wait_for_selector('text=mesh (без хаба)', timeout=20_000)
        hold(page, 4.0)  # 90 ₽ / 3 ч против хаба 135,50 ₽ / 30 ч
        click(page, 'button[data-action="assemble"]', 0.4)
        page.wait_for_selector('button[data-action="issue"]', timeout=30_000)
        hold(page, 3.5)
        click(page, 'button[data-action="issue"]', 0.4)
        page.wait_for_selector('text=Выдан', timeout=30_000)
        hold(page, 3.0)

        # начисления: четыре стороны и сходимость ledger
        caption(page, "Кадр 1 · Начисления: 4 стороны и сходимость ledger")
        click(page, 'button[data-action="ledger"]', 1.2)
        page.wait_for_selector('text=Начисления по заказу', timeout=20_000)
        smooth_scroll(page, 'text=Начисления по заказу', "start")
        hold(page, 7.0)

        # ---------- Кадр 2. Экономика и зелёная зона ----------
        goto_page(page, "economics", "Кадр 2 · Экономика: зелёная зона 4 сторон", 2.0)
        page.wait_for_selector("text=зелёная зона", timeout=20_000)
        hold(page, 3.0)
        smooth_scroll(page, "text=Зелёная зона 4 сторон", "start")
        hold(page, 6.0)  # ДА, 2 629 ₽, 129 ₽, +0,50 ₽

        # ползунок «Бонус ПВЗ за заказ без хаба»: 15 → 5 ₽, пересчёт через API
        caption(page, "Кадр 2 · Бонус ПВЗ за заказ без хаба: 15 → 5 ₽ (пересчёт через API)")
        page.evaluate("() => document.getElementById('econ-mesh_bonus').scrollIntoView({block:'center'})")
        hold(page, 1.0)
        box = page.locator("#econ-mesh_bonus").bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] / 2, steps=12)
        hold(page, 1.0)
        page.locator("#econ-mesh_bonus").evaluate(
            """async (el) => {
                 for (let v = 15; v >= 5; v--) {
                   el.value = String(v);
                   el.dispatchEvent(new Event('input', {bubbles: true}));
                   await new Promise(r => setTimeout(r, 170));
                 }
               }"""
        )
        hold(page, 2.5)  # дебаунс 350 мс + ответ API
        page.wait_for_function(
            "() => document.getElementById('econ-mesh_bonus').value === '5'", timeout=20_000
        )
        smooth_scroll(page, "text=Зелёная зона 4 сторон", "start")
        hold(page, 6.0)  # цифры после пересчёта должны прочитаться

        # ---------- Кадр 3. Демо-кокпит, шаг 4 ----------
        goto_page(page, "demo", "Кадр 3 · Демо-кокпит: шаг «4. Ошибка в логистике / отказ узла»", 2.0)
        page.wait_for_selector('button[data-demo-step="down"]', timeout=20_000)
        hold(page, 2.5)
        click(page, 'button[data-demo-step="down"]', 0.5)
        page.wait_for_function(
            """() => { const b = document.querySelector('button[data-demo-step="down"]');
                       return b && /PASS|FAIL/.test(b.closest('.demo-step').querySelector('.badge').textContent); }""",
            timeout=90_000,
        )
        hold(page, 3.5)

        caption(page, "Кадр 3 · PASS и «Лог различий» — PASS/FAIL считается по полям ответа API")
        smooth_scroll(page, 'h2:text-is("Лог различий")', "start")
        hold(page, 4.0)

        # добить хронометраж до целевого (требование 1–3 минуты, цель 100–150 с)
        elapsed = time.monotonic() - started
        if elapsed < TARGET_S:
            hold(page, TARGET_S - elapsed)
        caption(page, "")

        video = page.video.path()
        context.close()
        browser.close()
        return Path(video)


def encode(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(src),
            "-an",
            "-c:v", "libx264", "-preset", "medium", "-crf", CRF,
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-movflags", "+faststart",
            str(dst),
        ],
        check=True,
    )


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    stream = next(s for s in data["streams"] if s["codec_type"] == "video")
    return {
        "duration": float(data["format"]["duration"]),
        "codec": stream["codec_name"],
        "width": stream["width"],
        "height": stream["height"],
        "size_mb": path.stat().st_size / 1024 / 1024,
    }


def main() -> int:
    health = preflight()
    print(f"стенд чистый: заказов {health['counts']['orders']}, ПВЗ {health['counts']['pvz']}", file=sys.stderr)

    work = Path(tempfile.mkdtemp(prefix="demo-video-"))
    try:
        raw = record(work)
        encode(raw, OUT)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    info = probe(OUT)
    print(
        f"{OUT} — {info['size_mb']:.1f} МБ, {info['duration']:.1f} с, "
        f"{info['codec']} {info['width']}x{info['height']}"
    )
    if not MIN_S <= info["duration"] <= MAX_S:
        print(f"длительность вне {MIN_S}–{MAX_S} с", file=sys.stderr)
        return 1
    if (info["codec"], info["width"], info["height"]) != ("h264", W, H):
        print("кодек/разрешение не те — ожидались h264 и 1440x900", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
