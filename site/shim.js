(function(){
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
