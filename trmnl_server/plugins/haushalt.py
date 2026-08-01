"""Renders the Haushalts-Board (household weekly board) for the e-ink display.

Weekly grid: Mo..So columns, each showing the fixed block for that day (Sport,
Hobby-Tag, ...), tasks assigned to that day, and — if Google Calendar is
configured — that day's calendar events. Tasks without a specific day show in
a strip below the grid. Regenerated on demand whenever something changes via
the API (see routes/haushalt.py); REFRESH_INTERVAL here is just a fallback.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, timedelta
from typing import List, Optional

from PIL import Image, ImageDraw

from .. import google_calendar, haushalt_store
from .base import PluginBase, PluginOutput

logger = logging.getLogger(__name__)

CANVAS_SIZE = (800, 480)
MARGIN = 20
COL_GAP = 4
GRID_TOP = 56
GRID_BOTTOM = 408
UNASSIGNED_TOP = 416

PERSON_ICON = {"jonathan": "J", "katarina": "K", "kids": "•"}


class HaushaltPlugin(PluginBase):
    """Weekly household board: Mo..So with blocks, tasks, and calendar events."""

    BASENAME = "haushalt"
    OUTPUT_SUBDIR = "haushalt"
    DISPLAY_NAME = "Haushalts-Board"
    REGISTRY_ORDER = 10
    SET_PRIMARY = True
    # Safety-net only: normal updates happen instantly via routes/haushalt.py
    # calling process_plugin_output() after every change on the phone.
    REFRESH_INTERVAL = 6 * 3600

    def get_content_ttl(self) -> int:
        return 6 * 3600

    async def run(self, **kwargs) -> Optional[PluginOutput]:
        output_dir = kwargs.get("output_dir", "web")
        os.makedirs(output_dir, exist_ok=True)

        week = await haushalt_store.get_current_week()
        blocks = await haushalt_store.get_blocks()

        monday = _monday_of_current_week()
        calendar_events = {}
        if google_calendar.is_configured():
            calendar_events = await google_calendar.get_week_events(monday)

        image = await asyncio.to_thread(self._render, week, blocks, monday, calendar_events)
        output = await asyncio.to_thread(self.save_assets, image, output_dir, self.BASENAME)
        logger.info(
            "Haushalt board rendered to %s and %s",
            output.monochrome_path,
            output.grayscale_path,
        )
        return output

    def _render(self, week: dict, blocks: list, monday: date, calendar_events: dict) -> Image.Image:
        image = Image.new("L", CANVAS_SIZE, color=255)
        draw = ImageDraw.Draw(image)

        title_font = self.load_font(26)
        meta_font = self.load_font(14)
        day_font = self.load_font(15)
        block_font = self.load_font(12)
        item_font = self.load_font(11)
        strip_header_font = self.load_font(15)
        strip_item_font = self.load_font(13)

        # Header
        draw.text((MARGIN, 14), "Unsere Woche zuhause", fill=0, font=title_font)
        label = week.get("label", "")
        bbox = draw.textbbox((0, 0), label, font=meta_font)
        draw.text((CANVAS_SIZE[0] - MARGIN - (bbox[2] - bbox[0]), 20), label, fill=90, font=meta_font)
        draw.line([(MARGIN, 48), (CANVAS_SIZE[0] - MARGIN, 48)], fill=0, width=2)

        # Build per-day task lists (with owner) from jonathan/katarina/kids columns
        tasks_by_day: dict[int, list[tuple[str, dict]]] = {i: [] for i in range(7)}
        unassigned: list[tuple[str, dict]] = []
        for column in ("jonathan", "katarina", "kids"):
            for task in week.get(column, []):
                day = task.get("day")
                if day is None:
                    unassigned.append((column, task))
                else:
                    tasks_by_day.setdefault(day, []).append((column, task))

        blocks_by_day: dict[int, list[str]] = {}
        for b in blocks:
            blocks_by_day.setdefault(b["day"], []).append(b["label"])

        # 7-day grid
        usable_width = CANVAS_SIZE[0] - 2 * MARGIN - 6 * COL_GAP
        col_width = usable_width / 7

        for day_idx, day_short in enumerate(haushalt_store.DAY_NAMES):
            x0 = MARGIN + day_idx * (col_width + COL_GAP)
            day_date = monday + timedelta(days=day_idx)
            is_today = day_date == date.today()

            if is_today:
                draw.rectangle([x0 - 2, GRID_TOP - 4, x0 + col_width + 2, GRID_BOTTOM], outline=0, width=1)

            y = GRID_TOP
            draw.text((x0, y), f"{day_short} {day_date.day:02d}.{day_date.month:02d}", fill=0, font=day_font)
            y += day_font.size + 6

            for block_label in blocks_by_day.get(day_idx, []):
                draw.rectangle([x0, y, x0 + col_width, y + block_font.size + 6], fill=0)
                draw.text((x0 + 3, y + 2), self._truncate(block_label, block_font, col_width - 6), fill=255, font=block_font)
                y += block_font.size + 10

            if y < GRID_BOTTOM - item_font.size:
                draw.line([(x0, y), (x0 + col_width, y)], fill=210, width=1)
                y += 4

            items = tasks_by_day.get(day_idx, [])
            shown = 0
            max_items = max(0, int((GRID_BOTTOM - y) / (item_font.size + 5)) - 1)
            for column, task in items:
                if shown >= max_items:
                    break
                icon = PERSON_ICON.get(column, "?")
                marker = "\u2713" if task.get("done") else "\u25cb"
                text = f"{marker}{icon} {task['text']}"
                fill = 150 if task.get("done") else 0
                draw.text((x0, y), self._truncate(text, item_font, col_width), fill=fill, font=item_font)
                y += item_font.size + 5
                shown += 1

            for event_text in calendar_events.get(day_idx, []):
                if shown >= max_items:
                    break
                draw.text((x0, y), self._truncate(f"\u25a1 {event_text}", item_font, col_width), fill=60, font=item_font)
                y += item_font.size + 5
                shown += 1

            remaining = len(items) + len(calendar_events.get(day_idx, [])) - shown
            if remaining > 0 and y < GRID_BOTTOM:
                draw.text((x0, y), f"+{remaining} mehr", fill=140, font=item_font)

        # Vertical separators between day columns
        for day_idx in range(1, 7):
            x = MARGIN + day_idx * (col_width + COL_GAP) - COL_GAP / 2
            draw.line([(x, GRID_TOP - 4), (x, GRID_BOTTOM)], fill=225, width=1)

        # Unassigned tasks strip
        draw.line([(MARGIN, UNASSIGNED_TOP - 6), (CANVAS_SIZE[0] - MARGIN, UNASSIGNED_TOP - 6)], fill=0, width=1)
        draw.text((MARGIN, UNASSIGNED_TOP), "Diese Woche (kein fester Tag)", fill=0, font=strip_header_font)
        text_line = "   ".join(
            f"{'✓' if t.get('done') else '○'}{PERSON_ICON.get(c,'?')} {t['text']}" for c, t in unassigned
        ) or "—"
        wrapped = self._wrap(text_line, strip_item_font, CANVAS_SIZE[0] - 2 * MARGIN)
        y = UNASSIGNED_TOP + strip_header_font.size + 6
        for line in wrapped[:2]:
            draw.text((MARGIN, y), line, fill=0, font=strip_item_font)
            y += strip_item_font.size + 6

        return image

    @staticmethod
    def _truncate(text: str, font, max_width: float) -> str:
        if font.getlength(text) <= max_width:
            return text
        while text and font.getlength(text + "…") > max_width:
            text = text[:-1]
        return text + "…"

    @staticmethod
    def _wrap(text: str, font, max_width: int) -> List[str]:
        words = text.split()
        lines: List[str] = []
        current: List[str] = []
        for word in words:
            candidate = " ".join(current + [word])
            if font.getlength(candidate) <= max_width or not current:
                current.append(word)
            else:
                lines.append(" ".join(current))
                current = [word]
        if current:
            lines.append(" ".join(current))
        return lines


def _monday_of_current_week() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())
