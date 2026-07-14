# -*- coding: utf-8 -*-
"""
DiaMove Simulator builder (route_map.pdf 背景方式)

route_map.pdf の配線図をそのまま背景画像として用い、その上に運転整理ダイヤ
(tmp.xls) の各編成を時系列でアニメーション表示する自己完結 HTML を生成する。
編成数は tmp.xls の列数から自動検出するので、編成を増減したダイヤでも再実行のみで対応。

- 配線図の「形」は route_map.pdf と完全に同一(PDF をラスタライズして埋め込み)
- 列車位置は PDF から抽出した各ブロックのラベル座標に基づき線路上に配置
- ブロックのつながりは route_map_graph.py の隣接リストと一致

入力 : route_map.pdf, tmp.xls, route_map_graph.py
出力 : diamove_sim.html
"""
import base64
import importlib.util
import json
import os

import fitz  # PyMuPDF

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- route_map_graph.py ------------------------------------------------------
spec = importlib.util.spec_from_file_location("rmg", os.path.join(HERE, "route_map_graph.py"))
rmg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rmg)

# ---- tmp.xls (タブ区切りテキスト) --------------------------------------------
# 形式: 時刻(1列) + 距離(N列) + 空(1列) + ブロック(N列) + 空(1列) = 2N+3 列。
# 編成数 N は列数から自動検出するので、編成を増減したダイヤでもそのまま動く。
rawrows = []
with open(os.path.join(HERE, "tmp.xls"), encoding="utf-8") as f:
    for line in f:
        p = line.rstrip("\n").split("\t")
        if not p or p[0] == "":
            continue
        rawrows.append(p)
if not rawrows:
    raise SystemExit("tmp.xls にデータ行がありません")

# 最頻の列数を採用 (末尾タブの有無のばらつきに耐える)
from collections import Counter
ncol = Counter(len(p) for p in rawrows).most_common(1)[0][0]
if (ncol - 3) % 2 != 0 or ncol < 5:
    raise SystemExit(f"想定外の列数 {ncol}: 時刻+距離N+空+ブロックN+空 (=2N+3) の形式ではない")
NTRAIN = (ncol - 3) // 2
BLK_COLS = list(range(NTRAIN + 2, 2 * NTRAIN + 2))   # ブロック番号列の位置
print(f"編成数を自動検出: N={NTRAIN} (列数 {ncol}, ブロック列 {BLK_COLS[0]}〜{BLK_COLS[-1]})")

times, train_blocks = [], [[] for _ in range(NTRAIN)]
for p in rawrows:
    if len(p) < BLK_COLS[-1] + 1:
        continue
    times.append(float(p[0]))
    for t, c in enumerate(BLK_COLS):
        train_blocks[t].append(int(float(p[c])))
NSTEP = len(times)

# ---- route_map.pdf : 背景ラスタライズ + ブロック座標抽出 ----------------------
R = 3.0                                # ラスタライズ倍率
CLIP = fitz.Rect(0, 222, 841.92, 395)  # 配線図 + ラベル + 凡例を含む帯
TOPY, BOTY = 253.5, 329.0              # 本線(上り/下り)の線路 y 座標 [pt]

doc = fitz.open(os.path.join(HERE, "route_map.pdf"))
pg = doc[0]

# 背景 PNG (PDF 点座標 -> 画像px は ×R、クリップ原点を差し引く)
pix = pg.get_pixmap(matrix=fitz.Matrix(R, R), clip=CLIP)
bg_png = pix.tobytes("png")
bg_b64 = base64.b64encode(bg_png).decode("ascii")
IMG_W, IMG_H = pix.width, pix.height

# 数字ラベルの中心座標
label = {}
for w in pg.get_text("words"):
    t = w[4].strip()
    if t.isdigit():
        label[int(t)] = ((w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0)

# ブロック座標 [pt] : 本線は線路 y に補正、支線・車庫はラベル位置をそのまま使用
pt = {}
for n in range(1, 123):
    pt[n] = (label[n][0], TOPY if n % 2 == 1 else BOTY)
for n in list(range(123, 141)) + [127, 128]:
    pt[n] = label[n]
assert all(n in pt for n in range(1, 141)), "座標未定義のブロックがある"

# 画像 px 座標へ変換(クリップ原点を引いて ×R)
coords_px = {
    n: ((x - CLIP.x0) * R, (y - CLIP.y0) * R)
    for n, (x, y) in pt.items()
}

# ダイヤで使われるブロックが全て配線図に存在するか確認
used = sorted({b for seq in train_blocks for b in seq})
missing = [b for b in used if b not in coords_px]
if missing:
    raise SystemExit(f"配線図に座標のないブロックがダイヤに含まれます: {missing}")

# ---- データ ------------------------------------------------------------------
data = {
    "img_w": IMG_W,
    "img_h": IMG_H,
    "nstep": NSTEP,
    "step_minutes": 0.25,                 # 1ステップ = 15 秒
    "coords": {str(k): [round(v[0], 1), round(v[1], 1)] for k, v in coords_px.items()},
    "trains": train_blocks,               # [編成][ステップ] = ブロック番号
}

# ---- HTML --------------------------------------------------------------------
HTML = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>運行シミュレーション — DiaMove Simulator</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  html, body { margin: 0; min-height: 100%; font-family: "Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif;
               background: #f4f6fa; color: #1c2536; }
  #app { display: flex; flex-direction: column; min-height: 100vh; }
  header { padding: 10px 18px; background: #1b2a44; color: #fff;
           display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }
  header h1 { font-size: 18px; margin: 0; font-weight: 700; letter-spacing: .03em; }
  header .sub { font-size: 12px; color: #aebfdc; }
  #clock { font-size: 30px; font-weight: 700; font-variant-numeric: tabular-nums;
           margin-left: auto; color: #ffd54a; letter-spacing: .02em; }
  #canvasWrap { flex: 1 1 auto; min-height: 0; position: relative; background: #fff;
                height: 70vh; }
  canvas { display: block; width: 100%; height: 100%; }
  footer { padding: 10px 18px; background: #eef1f7; border-top: 1px solid #d4dbe8;
           display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  button { background: #2b6cf0; color: #fff; border: 0; border-radius: 7px;
           padding: 9px 18px; font-size: 15px; font-weight: 700; cursor: pointer; }
  button:hover { background: #3f7dff; }
  button.sec { background: #d3dbe9; color: #1c2536; }
  button.sec:hover { background: #c2cde0; }
  label { font-size: 13px; color: #45526b; display: flex; align-items: center; gap: 7px; }
  input[type=range] { width: 320px; max-width: 40vw; accent-color: #2b6cf0; }
  #speed { width: 150px; accent-color: #f08a2b; }
  .legend { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
  .legend .item { display: flex; align-items: center; gap: 6px; font-size: 13px; }
  .legend .dot { width: 14px; height: 14px; border-radius: 50%; border: 2px solid #fff;
                 box-shadow: 0 0 0 1px #888; }
  .speedval { color: #d9772a; font-weight: 700; min-width: 92px; }
</style>
</head>
<body>
<div id="app">
  <header>
    <h1>運行シミュレーション</h1>
    <span class="sub">運転整理ダイヤの再現 — 配線図上の列車位置</span>
    <span id="clock">00:00:00</span>
  </header>
  <div id="canvasWrap"><canvas id="cv"></canvas></div>
  <footer>
    <button id="play">▶ 再生</button>
    <button id="reset" class="sec">⟲ 先頭へ</button>
    <label>時刻 <input id="seek" type="range" min="0" max="100" value="0" step="0.01"></label>
    <label>速度 <input id="speed" type="range" min="1" max="600" value="120" step="1">
      <span class="speedval" id="speedval">120倍</span></label>
    <div class="legend" id="legend"></div>
  </footer>
</div>
<script>
const DATA = __DATA__;
const BG_SRC = "data:image/png;base64,__BG__";
// 編成色: 見やすい基本パレット。編成数がこれを超えたら色相環で自動生成。
const BASE_COLORS = ["#e8392b","#1ea85a","#1f6fe0","#e8a200","#9b34d6",
                     "#00a6a6","#d6336c","#7a8a00","#5a3fc0","#c25e00"];
function colorFor(i){
  if (i < BASE_COLORS.length) return BASE_COLORS[i];
  const h = (i * 137.508) % 360;          // 黄金角で均等に散らす
  return "hsl(" + h.toFixed(1) + ",70%,45%)";
}

const cv = document.getElementById("cv");
const ctx = cv.getContext("2d");
const bg = new Image();
let bgReady = false;
bg.onload = () => { bgReady = true; };
bg.src = BG_SRC;

// 画像px -> キャンバスpx の変換 (アスペクト比保持で内接)
let view = { s: 1, ox: 0, oy: 0, dpr: 1 };
function layout(){
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  cv.width = w * dpr; cv.height = h * dpr;
  const s = Math.min(w / DATA.img_w, h / DATA.img_h);
  view.s = s; view.dpr = dpr;
  view.ox = (w - DATA.img_w * s) / 2;
  view.oy = (h - DATA.img_h * s) / 2;
}
function CX(x){ return (x * view.s + view.ox) * view.dpr; }
function CY(y){ return (y * view.s + view.oy) * view.dpr; }
function PX(v){ return v * view.s * view.dpr; }

function blockXY(b){ const c = DATA.coords[String(b)]; return c ? {x:c[0], y:c[1]} : null; }
function trainPos(i, t){
  const seq = DATA.trains[i], n = seq.length;
  let k = Math.floor(t); if (k < 0) k = 0; if (k > n-2) k = n-2;
  let f = t - k; if (f < 0) f = 0; if (f > 1) f = 1;
  const a = blockXY(seq[k]), b = blockXY(seq[k+1]);
  if(!a) return b; if(!b) return a;
  return { x: a.x + (b.x-a.x)*f, y: a.y + (b.y-a.y)*f };
}

function render(t){
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (bgReady){
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(bg, view.ox*view.dpr, view.oy*view.dpr,
                  DATA.img_w*view.s*view.dpr, DATA.img_h*view.s*view.dpr);
  }
  for (let i = 0; i < DATA.trains.length; i++){
    const p = trainPos(i, t); if(!p) continue;
    const x = CX(p.x), y = CY(p.y), r = Math.max(12, PX(16));
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI*2);
    ctx.fillStyle = colorFor(i);
    ctx.shadowColor = "rgba(0,0,0,0.35)"; ctx.shadowBlur = PX(5); ctx.shadowOffsetY = PX(1.5);
    ctx.fill(); ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
    ctx.lineWidth = Math.max(2, PX(2.2)); ctx.strokeStyle = "#fff"; ctx.stroke();
    ctx.fillStyle = "#fff"; ctx.font = "700 " + Math.max(14, PX(16)) + "px sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(String(i+1), x, y + PX(0.5));
  }
}

function fmtClock(stepT){
  const sec = stepT * DATA.step_minutes * 60;
  const z = v => String(v).padStart(2, "0");
  return z(Math.floor(sec/3600)) + ":" + z(Math.floor(sec/60)%60) + ":" + z(Math.floor(sec)%60);
}

let t = 0;
const MAXT = DATA.nstep - 1;
let playing = false, speed = 120, last = null;
const elClock = document.getElementById("clock");
const elSeek = document.getElementById("seek");
const elPlay = document.getElementById("play");
const elSpeed = document.getElementById("speed");
const elSpeedVal = document.getElementById("speedval");

function syncUI(){ elClock.textContent = fmtClock(t); elSeek.value = (t/MAXT*100).toFixed(3); }
function frame(ts){
  if (last === null) last = ts;
  const dt = (ts - last) / 1000; last = ts;
  if (playing){
    t += (dt * speed / 60) / DATA.step_minutes;
    if (t >= MAXT){ t = MAXT; playing = false; elPlay.textContent = "▶ 再生"; }
  }
  render(t); syncUI();
  requestAnimationFrame(frame);
}
elPlay.onclick = () => { if (t >= MAXT) t = 0; playing = !playing; elPlay.textContent = playing ? "⏸ 一時停止" : "▶ 再生"; };
document.getElementById("reset").onclick = () => { t = 0; playing = false; elPlay.textContent = "▶ 再生"; };
elSeek.oninput = () => { t = parseFloat(elSeek.value)/100*MAXT; };
elSpeed.oninput = () => { speed = parseInt(elSpeed.value); elSpeedVal.textContent = speed + "倍"; };
window.addEventListener("resize", layout);
document.addEventListener("keydown", e => { if (e.code === "Space"){ e.preventDefault(); elPlay.click(); } });

const lg = document.getElementById("legend");
for (let i = 0; i < DATA.trains.length; i++){
  const d = document.createElement("div"); d.className = "item";
  d.innerHTML = '<span class="dot" style="background:'+colorFor(i)+'"></span>列車'+(i+1);
  lg.appendChild(d);
}

layout(); syncUI(); requestAnimationFrame(frame);
</script>
</body>
</html>
"""

out = HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
out = out.replace("__BG__", bg_b64)
path = os.path.join(HERE, "diamove_sim.html")
with open(path, "w", encoding="utf-8") as f:
    f.write(out)

print("生成:", path)
print(f"  背景画像 {IMG_W}x{IMG_H}px  ステップ {NSTEP}  編成 {NTRAIN}")
print(f"  HTML サイズ: {len(out)//1024} KB")
