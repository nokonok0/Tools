import math
import os

from PIL import Image, ImageDraw, ImageFont

COLORS = {"ready": "#32d5ae", "exhausted": "#ff6978",
          "learning": "#ffd166", "stale": "#b4bec9", "unavailable": "#b4bec9"}


def icon_text(result):
    """アイコンに表示する枯渇までの推定時間。"""
    status = result.get("status")
    hours = result.get("hours")
    if status == "exhausted":
        return "0m"
    if status in ("ready", "stale") and hours is not None:
        return time_label(hours)
    if status == "learning":
        return "..."
    return "--"


def time_label(hours):
    if hours is None:
        return "--"
    if hours <= 0:
        return "0m"
    minutes = max(1, math.ceil(hours * 60))
    if minutes < 60:
        return f"{minutes}m"
    value = minutes / 60
    if value < 10:
        return f"{value:.1f}".removesuffix(".0") + "h"
    return f"{math.ceil(value)}h" if value <= 99 else "99+"


def make_icon(label, status):
    image = Image.new("RGBA", (64, 64))
    draw = ImageDraw.Draw(image)
    color = COLORS.get(status, COLORS["stale"])
    draw.rounded_rectangle((0, 2, 63, 61), radius=10, fill="#14232a")
    font_path = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "segoeuib.ttf")
    font = ImageFont.load_default(size=16)
    box = draw.textbbox((0, 0), label, font=font)
    for size in range(46, 15, -1):
        try:
            font = ImageFont.truetype(font_path, size)
        except OSError:
            font = ImageFont.load_default(size=size)
        box = draw.textbbox((0, 0), label, font=font)
        if box[2] - box[0] <= 60:
            break
    width, height = box[2] - box[0], box[3] - box[1]
    draw.text(((64 - width) / 2 - box[0], (60 - height) / 2 - box[1]),
              label, font=font, fill=color)
    draw.line((15, 57, 48, 57), fill=color, width=3)
    return image
