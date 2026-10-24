"""Approach B (grounding) and the gate for C, via Ollama qwen2.5vl:7b. THROWAWAY."""
import base64, io, json, re
import requests
from PIL import Image

URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen2.5vl:7b"
SIDE = 812
TARGET_PX = 820_000


def _b64(im: Image.Image) -> str:
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def _chat(prompt: str, im: Image.Image, fmt=None, timeout=120):
    body = {"model": MODEL, "stream": False, "options": {"temperature": 0, "seed": 1, "num_ctx": 4096},
            "messages": [{"role": "user", "content": prompt, "images": [_b64(im)]}]}
    if fmt:
        body["format"] = fmt
    r = requests.post(URL, json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()["message"]["content"]


def _prep(path: str):
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        # Ollama rescales every image to ~830k pixels and the model answers in that frame, so send that size already
        s = (TARGET_PX / (w * h)) ** 0.5
        return im.copy(), w, h, im.resize((max(28, round(w * s / 28) * 28), max(28, round(h * s / 28) * 28)))


PROMPT = ("This image is a meme, possibly screenshotted with extra frames, borders, phone status bars, app buttons or "
          "other interface around it. Give the bounding box of the meme content only (the picture and its own captions), "
          "excluding any such extras. Answer with JSON only: {\"bbox_2d\": [x1, y1, x2, y2]} in pixel coordinates of this image.")


def ground(path: str):
    full, w, h, small = _prep(path)
    txt = _chat(PROMPT, small)
    m = re.search(r"\[\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\]", txt)
    if not m:
        return None
    sw, sh = small.size
    x0, y0, x1, y1 = (int(g) for g in m.groups())
    box = (max(0, round(x0 / sw * w)), max(0, round(y0 / sh * h)), min(w, round(x1 / sw * w)), min(h, round(y1 / sh * h)))
    if box[2] - box[0] < 40 or box[3] - box[1] < 40:
        return None
    return {"box": box, "size": (w, h), "raw": txt[:120]}


GATE_SCHEMA = {"type": "object", "properties": {"extras": {"type": "boolean"}}, "required": ["extras"]}
GATE_Q = ("Does this image contain extra interface around the meme itself, such as phone status bar, app toolbar, buttons, "
          "window frame, or a thick border added around the picture? Plain meme captions do not count. Answer JSON {\"extras\": true|false}.")


def has_extras(im: Image.Image) -> bool:
    im = im.copy(); im.thumbnail((SIDE, SIDE))
    return bool(json.loads(_chat(GATE_Q, im, fmt=GATE_SCHEMA)).get("extras"))
