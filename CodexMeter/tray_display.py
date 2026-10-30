import math
import os

from PIL import Image, ImageDraw, ImageFont


def time_label(result):
    if result["status"] != "ready" and not (result.get("historical") and "hours" in result):
        return {"learning": "...", "stale": "--", "unavailable": "--",
                "exhausted": "0m"}[result["status"]]
    if result["hours"] <= 0 and result["status"] != "ready":
        return "0m"
    minutes = max(1, math.ceil(result["hours"] * 60))
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes / 60
    if hours < 10:
        return f"{hours:.1f}".removesuffix(".0") + "h"
    return f"{math.ceil(hours)}h" if hours <= 99 else "99+"


def make_icon(label, status):
    image = Image.new("RGBA", (64, 64))
    draw = ImageDraw.Draw(image)
    color = {"ready": "#32d5ae", "exhausted": "#ff6978",
             "learning": "#ffd166"}.get(status, "#b4bec9")
    draw.rounded_rectangle((0, 2, 63, 61), radius=10, fill="#14232a")
    font_path = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "segoeuib.ttf")
    for size in range(48, 15, -1):
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
