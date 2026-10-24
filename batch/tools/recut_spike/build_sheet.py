"""Pool the approaches' top proposals + random no-proposal images into one blinded HTML sheet. THROWAWAY."""
import base64, html, io, json, random
from pathlib import Path
from PIL import Image, ImageDraw

O = Path("evaluation/work/recut_spike")
s = {i["id"]: i for i in json.loads((O / "sample.json").read_text(encoding="utf-8"))["items"]}
a = json.loads((O / "result_a.json").read_text(encoding="utf-8"))
bc = {r["id"]: r for r in (json.loads(l) for l in (O / "result_bc.jsonl").read_text(encoding="utf-8").splitlines() if l)}
rng = random.Random(5)
TOP = 40


def ratio(box, size): return (box[2]-box[0])*(box[3]-box[1])/(size[0]*size[1])
def iou(p, q):
    ix = max(0, min(p[2], q[2]) - max(p[0], q[0])); iy = max(0, min(p[3], q[3]) - max(p[1], q[1]))
    i = ix * iy; u = (p[2]-p[0])*(p[3]-p[1]) + (q[2]-q[0])*(q[3]-q[1]) - i
    return i / u if u else 0

ids = list(bc)
A = sorted([i for i in ids if a.get(i) and "box" in a[i]], key=lambda i: -a[i]["confidence"])
B = [i for i in ids if bc[i].get("b") and 0.3 <= ratio(bc[i]["b"]["box"], bc[i]["b"]["size"]) < 0.92]
rng.shuffle(B)
C = [i for i in A if bc[i].get("gate_orig") and bc[i].get("gate_crop") is False]
sel = {"A": A[:TOP], "B": B[:TOP], "C": C[:TOP]}
items = []  # (image id, box, approaches)
for name, lst in sel.items():
    for i in lst:
        box = tuple(bc[i]["b"]["box"]) if name == "B" else tuple(a[i]["box"])
        for it in items:
            if it["id"] == i and iou(it["box"], box) > 0.9:
                it["by"].add(name); break
        else:
            items.append({"id": i, "box": box, "by": {name}})
proposed = {i for i in ids if (a.get(i) and "box" in a[i]) or (bc[i].get("b") and ratio(bc[i]["b"]["box"], bc[i]["b"]["size"]) < 0.92)}
neg = [i for i in ids if i not in proposed]; rng.shuffle(neg)
for i in neg[:20]:
    items.append({"id": i, "box": None, "by": {"none"}})
rng.shuffle(items)


def b64(im, w=420):
    im = im.copy(); im.thumbnail((w, w)); buf = io.BytesIO(); im.convert("RGB").save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

cards, key = [], {}
for n, it in enumerate(items):
    key[f"i{n}"] = {"id": it["id"], "box": it["box"], "by": sorted(it["by"])}
    with Image.open(s[it["id"]]["path"]) as im:
        im = im.convert("RGB")
        if it["box"]:
            d = im.copy(); ImageDraw.Draw(d).rectangle(it["box"], outline=(255, 0, 0), width=max(3, im.width // 120))
            right = f'<img src="{b64(im.crop(it["box"]))}"><div>cut</div>'
            q = [("2", "use as is"), ("1", "slightly off"), ("0", "wrong"), ("s", "skip")]
            left = b64(d)
        else:
            right = "<div>no proposal</div>"; q = [("y", "needs a cut"), ("n", "fine as is"), ("s", "skip")]
            left = b64(im)
    radios = "".join(f'<label><input type=radio name=i{n} value={v}> {t}</label> ' for v, t in q)
    cards.append(f'<div class=c><div class=im><div><img src="{left}"><div>source{" + box" if it["box"] else ""}</div></div><div>{right}</div></div><div class=q>#{n + 1} {radios}</div></div>')

page = f"""<!doctype html><meta charset=utf-8><title>Recut spike sheet</title>
<style>body{{font:14px sans-serif;max-width:980px;margin:auto}}.c{{border:1px solid #888;margin:12px 0;padding:8px}}.im{{display:flex;gap:12px}}img{{max-width:420px;max-height:420px}}.q label{{margin-right:14px}}</style>
<h2>Is the red box the right cut? ({len(items)} items)</h2><p>2 = use as is, 1 = slightly off, 0 = wrong. For items without a proposal: does it need a cut?</p>
<form id=f>{''.join(cards)}</form><button onclick="exp()">Export JSON</button> <span id=cnt></span>
<script>
const S='recutSpike';let st=JSON.parse(localStorage.getItem(S)||'{{}}');
for(const [k,v] of Object.entries(st)){{const e=document.querySelector(`input[name=${{k}}][value="${{v}}"]`);if(e)e.checked=true}}
document.getElementById('f').addEventListener('change',e=>{{st[e.target.name]=e.target.value;localStorage.setItem(S,JSON.stringify(st));cnt()}});
function cnt(){{document.getElementById('cnt').textContent=Object.keys(st).length+' / {len(items)} answered'}}cnt();
function exp(){{const b=new Blob([JSON.stringify(st,null,1)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='recut-spike-judgments.json';a.click()}}
</script>"""
(O / "sheet.html").write_text(page, encoding="utf-8")
(O / "sheet_key.json").write_text(json.dumps(key, default=list), encoding="utf-8")
print("items", len(items), {k: len(v) for k, v in sel.items()}, "size MB", round(len(page) / 1e6, 1))
