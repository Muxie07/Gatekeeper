"""Build the static GitHub Pages site (site/) from the live app's own files.
    python build_site.py            -> site/
Play yourself runs the REAL Python engine (gk/env.py) in the browser through Pyodide: no server,
no model, no quota. The recorded AI run is a REPLAY of a MOCK (simulated) run and is labelled so."""
import json, os, re, shutil
HERE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(HERE, "site")
os.makedirs(os.path.join(OUT, "engine"), exist_ok=True)
rd = lambda p: open(os.path.join(HERE, p), encoding="utf-8").read()
wr = lambda p, s: open(os.path.join(OUT, p), "w", encoding="utf-8").write(s)
shutil.copy(os.path.join(HERE, "web/game.js"), os.path.join(OUT, "game.js"))
shutil.copy(os.path.join(HERE, "gk/env.py"), os.path.join(OUT, "engine/env.py"))

def sub(s, old, new, count=1):
    assert old in s, "patch target missing: " + old[:60]
    return s.replace(old, new, count)

h = rd("web/index.html")
h = sub(h, '<script src="game.js"></script>', '<script>window.GK_STATIC=true</script>\n<script src="game.js"></script>\n<script src="shim.js"></script>')
h = sub(h, 'const REPLAY=new URLSearchParams(location.search).get("replay");',
        'const REPLAY=new URLSearchParams(location.search).get("replay")||"replay.json";')
h = sub(h, '<button id="tLive" class="on">Watch the AI</button> <button id="tHuman">Play yourself</button> <button id="tHist">History (replay)</button>',
        '<button id="tHuman" class="on">1 · Play it yourself</button> <button id="tLive">2 · Watch an agent + the gate (recorded)</button> <button id="tHist">3 · Honest summary</button>')
h = sub(h, '<span id="controls">', '<span id="controls" class="hidden">')
INTRO = ('<div id="intro" style="padding:10px 16px;border-bottom:1px solid var(--line);max-width:1100px">'
 '<b>Gatekeeper</b> — a promotion gate for self-improving LLM agents. An agent may only change its own instructions if the change '
 'survives held-out worlds, a placebo, a regression suite, an integrity check and a cost check. '
 '<span class="mut">Start with the game: it is the same Python engine the AI plays, running in your browser. Most people lose the first time (a zombie arrives at a fixed tick).</span></div>')
h = sub(h, '</header>', '</header>\n' + INTRO)
h = sub(h, 'setBadge("REPLAY (saved)","replay");loadHistory()', 'setBadge("SUMMARY","idle")')
SUMMARY = '''<div id="hist" class="hidden"><main><section class="card wide"><h2>Honest summary — what is real and what is not</h2><div>
<h3>REAL</h3><ul>
<li><b>The game.</b> "Play it yourself" runs <code>gk/env.py</code> (the exact engine file the agent plays) in your browser via Pyodide. No server, no model, no quota.</li>
<li><b>A real model baseline.</b> The unmodified agent harness was played by a real model, <code>gemini-3.5-flash-lite</code>, free tier, in 42 games on this engine (v4): regression 83% (6 games), report 60% (10), train 67% (6), held-out 38% (16), canary 50% (4). Its most common mistakes: not collecting enough materials, a zombie raid stealing its wood, and going to the mine without a pickaxe.</li>
<li><b>The gate.</b> Held-out paired permutation test, placebo, regression, integrity and cost checks, thresholds frozen in <code>prereg/PREREG.md</code> before the real runs.</li></ul>
<h3>SIMULATED (labelled everywhere it appears)</h3><ul>
<li>The recorded run in tab 2 is a replay of a <b>mock</b> run: a scripted stand-in agent and proposer, so the agent's moves, lessons and gate numbers shown there are <b>not</b> real model output.</li></ul>
<h3>NOT DONE YET</h3><ul>
<li>No real lesson written by a real model has been through the gate yet. The full real experiment (an accepted improvement, a rejected placebo, a rollback, original vs improved on unseen worlds) has not been run, so I make no such claim. The older v2 report is kept at <a href="report.html">report.html</a> and is from an earlier, simpler version of the game.</li></ul>
<h3>Limits</h3><ul><li>Small samples (tens of games per suite), one game family, one weak free-tier player model. Treat the baseline numbers as a starting point, not a benchmark.</li></ul>
<p class="mut">Code: <a href="https://github.com/Muxie07/gatekeeper">github.com/Muxie07/gatekeeper</a></p>
</div></section></main></div>'''
h = re.sub(r'<div id="hist" class="hidden">.*?</section></main></div>', lambda m: SUMMARY, h, count=1, flags=re.S)
h = sub(h, 'async function loadHistory(){', 'async function loadHistory(){return;')
# header/badge only describe the replay while the replay tab is showing
h = sub(h, 'function setBadge(text,mode){', 'function setBadge(text,mode){if(window.GK_TAB==="human"&&mode!=="human")return;if(window.GK_TAB==="hist"&&text!=="SUMMARY")return;')
h = sub(h, 'function tab(which){const w=which===true?"hist":which===false?"live":which;', 'function tab(which){const w=which===true?"hist":which===false?"live":which;window.GK_TAB=w;document.body.dataset.tab=w;')
h = sub(h, '</style>', 'body[data-tab=human] #replayCtl,body[data-tab=hist] #replayCtl,body[data-tab=human] #status,body[data-tab=hist] #status{display:none}</style>')
h = sub(h, '<head>', '<head><link rel="icon" href="data:,">')
# play yourself is the default tab
h = sub(h, 'if(REPLAY){startReplay()}else', 'if(REPLAY){startReplay();setTimeout(()=>tab("human"),0)}else')
h = sub(h, '<title>', '<title>') if '<title>' in h else h
wr("index.html", h)

SHIM = r'''(function(){
const PY=new URLSearchParams(location.search).get("pyodide")||"https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
let ready=null;
function load(){if(ready)return ready;ready=(async()=>{
  try{window.banner&&window.banner("Loading the game engine (a few MB, first visit only)…","warn")}catch(_){}
  await new Promise((res,rej)=>{const s=document.createElement("script");s.src=PY+"pyodide.js";s.onload=res;s.onerror=()=>rej(new Error("Could not load Pyodide"));document.head.appendChild(s)});
  const py=await loadPyodide({indexURL:PY});
  const src=await (await fetch("engine/env.py")).text();
  py.FS.mkdir("/gk");py.FS.writeFile("/gk/env.py",src);py.FS.writeFile("/gk/__init__.py","");
  await py.runPythonAsync(`
import sys, json, random
sys.path.insert(0, "/")
from gk.env import ACTIONS, MiniCraft
_n = random.randrange(100000)
_env = None
def _task(seed): return "sword" if random.Random(seed * 31 + 5).random() < 0.5 else "pickaxe"
def _state(action):
    e = _env
    return {"mode": "human", "suite": "human", "label": "you", "version": "-", "seed": e.seed, "task": e.task,
            "hazard": e.hazard, "max_steps": e.max_steps, "step": e.steps, "action": action, "raw": action,
            "outcome": e.last, "inventory": dict(e.inv), "sheltered": e.sheltered, "cue": e.cue(),
            "done": e.done(), "success": e.success(), "mistakes": list(e.events), "episode": "human:%d" % e.seed,
            "trace": e.last_trace if action else [], "world": e.snapshot(), "static": e.static(), "plan": e.last_plan}
def h_new(task):
    global _n, _env
    _n += 1; seed = 800000 + _n
    t = task if task in ("pickaxe", "sword") else _task(seed)
    _env = MiniCraft(seed, t, max_steps=14, hazard_p=0.5)
    return json.dumps(_state(""))
def h_act(a):
    if _env is None: return json.dumps({"_status": 409, "error": "start a new world first"})
    if _env.done(): return json.dumps({"_status": 409, "error": "this game is over; start a new world"})
    if a not in ACTIONS: return json.dumps({"_status": 400, "error": "unknown action"})
    _env.step(a)
    return json.dumps(_state(a))
`);
  return py})();return ready}
const of=window.fetch.bind(window);
window.fetch=async function(u,o){
  const url=typeof u==="string"?u:(u&&u.url)||"";
  if(url==="/human/new"||url==="/human/act"){
    try{const py=await load();const body=o&&o.body?JSON.parse(o.body):{};
      const out=JSON.parse(url==="/human/new"?py.globals.get("h_new")(body.task||""):py.globals.get("h_act")(body.action||""));
      const status=out._status||200;delete out._status;
      return new Response(JSON.stringify(out),{status,headers:{"Content-Type":"application/json"}})}
    catch(e){return new Response(JSON.stringify({error:"Could not start the in-browser engine: "+e.message}),{status:500,headers:{"Content-Type":"application/json"}})}}
  return of(u,o)};
})();
'''
wr("shim.js", SHIM)

# recorded run: the last complete 2-round mock run (labelled simulated by the page)
ev = [json.loads(l) for l in open(os.path.join(HERE, "events.mock.jsonl"), encoding="utf-8") if l.strip()]
starts = [i for i, e in enumerate(ev) if e.get("type") == "run_start"]
runs = [ev[a:b] for a, b in zip(starts, starts[1:] + [len(ev)])]
full = [r for r in runs if any(e.get("type") == "round_start" and e.get("round") == 2 for e in r)]
run = full[-1]
json.dump({"label": "REPLAY of a recorded MOCK (simulated) run - not a real model", "modes": ["mock"], "simulated": True, "events": run},
          open(os.path.join(OUT, "replay.json"), "w"), separators=(",", ":"))
open(os.path.join(OUT, ".nojekyll"), "w").close()
print("built site/:", sorted(os.listdir(OUT)), "replay events", len(run))
