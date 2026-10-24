"""Round 2: detector A proposals below confidence 1.0 (stratified margin bands + inner kind), blinded. THROWAWAY."""
import base64, io, json, random
from pathlib import Path
from PIL import Image, ImageDraw

O = Path("evaluation/work/recut_spike")
s = {i["id"]: i for i in json.loads((O / "sample.json").read_text(encoding="utf-8"))["items"]}
a = {k: v for k, v in json.loads((O / "result_a.json").read_text(encoding="utf-8")).items() if v and "box" in v}
judged = {v["id"] for v in json.loads((O / "sheet_key.json").read_text(encoding="utf-8")).values()}
rng = random.Random(23)
picks = []
for lo, hi in [(0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 0.9999)]:
    pool = [i for i, v in a.items() if v["kind"] == "margin" and lo <= v["confidence"] < hi and i not in judged]
    rng.shuffle(pool); picks += [(i, f"margin {lo}-{hi}") for i in pool[:12]]
pool = [i for i, v in a.items() if v["kind"] == "inner" and i not in judged]
rng.shuffle(pool); picks += [(i, "inner") for i in pool[:14]]
rng.shuffle(picks)


def b64(im, w=420):
    im = im.copy(); im.thumbnail((w, w)); buf = io.BytesIO(); im.convert("RGB").save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

cards, key = [], {}
for n, (i, band) in enumerate(picks):
    box = tuple(a[i]["box"]); key[f"i{n}"] = {"id": i, "box": box, "band": band, "confidence": a[i]["confidence"]}
    with Image.open(s[i]["path"]) as im:
        im = im.convert("RGB"); d = im.copy()
        ImageDraw.Draw(d).rectangle(box, outline=(255, 0, 0), width=max(3, im.width // 120))
        left, right = b64(d), b64(im.crop(box))
    radios = "".join(f'<label><input type=radio name=i{n} value={v}> {t}</label> ' for v, t in [("2", "use as is"), ("1", "slightly off"), ("0", "wrong"), ("s", "skip")])
    cards.append(f'<div class=c><div class=im><div><img src="{left}"><div>source + box</div></div><div><img src="{right}"><div>cut</div></div></div><div class=q>#{n + 1} {radios}</div></div>')

page = f"""<!doctype html><meta charset=utf-8><title>Recut spike sheet 2</title>
<style>body{{font:14px sans-serif;max-width:980px;margin:auto}}.c{{border:1px solid #888;margin:12px 0;padding:8px}}.im{{display:flex;gap:12px}}img{{max-width:420px;max-height:420px}}.q label{{margin-right:14px}}</style>
<h2>Round 2: is the red box the right cut? ({len(picks)} items)</h2><p>2 = use as is, 1 = slightly off (clips text or an important edge), 0 = wrong.</p>
<form id=f>{''.join(cards)}</form><button onclick="exp()">Export JSON</button> <span id=cnt></span>
<script>
const S='recutSpike2';let st=JSON.parse(localStorage.getItem(S)||'{{}}');
for(const [k,v] of Object.entries(st)){{const e=document.querySelector(`input[name=${{k}}][value="${{v}}"]`);if(e)e.checked=true}}
document.getElementById('f').addEventListener('change',e=>{{st[e.target.name]=e.target.value;localStorage.setItem(S,JSON.stringify(st));cnt()}});
function cnt(){{document.getElementById('cnt').textContent=Object.keys(st).length+' / {len(picks)} answered'}}cnt();
function exp(){{const b=new Blob([JSON.stringify(st,null,1)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='recut-spike2-judgments.json';a.click()}}
</script>"""
(O / "sheet2.html").write_text(page, encoding="utf-8")
(O / "sheet2_key.json").write_text(json.dumps(key), encoding="utf-8")
print("items", len(picks), "MB", round(len(page) / 1e6, 1))
