// MiniCraft v4 renderer for Gatekeeper.
//
// THE PYTHON ENGINE (gk/env.py) IS THE ONLY SOURCE OF GAME STATE. Every step event carries the
// engine's tick-by-tick trace (player and zombie positions, what happened, inventory, table,
// shelter, resource left) and the world snapshot after the step. This file only plays those
// frames back: it interpolates the sprite between two consecutive engine positions so the walk
// looks smooth, and after the last frame it replaces everything shown with the engine snapshot.
// It never chooses a path, a target or an outcome. `shown()` and `frameShown()` expose exactly
// what is on screen so tests can compare it with the backend events.
"use strict";
(function (global) {
  const U = 16, Z = 3, TS = U * Z;           // 16-unit pixel-art tiles drawn at 3x (48 px)
  const VW = 960, VH = 540;

  // ---------- pixel-art sprites, built once in memory (original art, drawn in code) ----------
  function spr(w, h, f) { const c = document.createElement("canvas"); c.width = w; c.height = h; const g = c.getContext("2d");
    f((x, y, ww, hh, col) => { g.fillStyle = col; g.fillRect(x, y, ww, hh); }, g); return c; }
  function rowsShape(p, rows, x0, y0, out, fill, hi) {   // rows: [offset, width] per line
    rows.forEach(([o, w], i) => { p(x0 + o - 1, y0 + i, w + 2, 1, out); });
    rows.forEach(([o, w], i) => { p(x0 + o, y0 + i, w, 1, fill); if (hi && i > 0 && i < rows.length - 2 && w > 4) p(x0 + o + 1, y0 + i, Math.max(1, (w / 3) | 0), 1, hi); });
  }
  const tri = (top, n, maxw) => Array.from({ length: n }, (_, i) => { const w = Math.max(2, Math.round(maxw * (i + 1) / n)); return [Math.round((maxw - w) / 2), w]; });
  const SP = {};
  function build() {
    SP.pine = spr(20, 30, p => {
      p(9, 24, 3, 6, "#5a3a1e"); p(10, 24, 1, 6, "#7a5330");
      [[0, 9, 12], [6, 10, 16], [12, 12, 20]].forEach(([y, n, w], k) => rowsShape(p, tri(0, n, w), (20 - w) / 2 | 0, y, "#173f22", k === 2 ? "#2b7a3a" : "#33904a", "#56b866"));
    });
    SP.pine2 = spr(20, 30, p => {
      p(9, 24, 3, 6, "#5a3a1e");
      [[0, 9, 12], [6, 10, 16], [12, 12, 20]].forEach(([y, n, w]) => rowsShape(p, tri(0, n, w), (20 - w) / 2 | 0, y, "#183a3a", "#2f6e6a", "#4f9a8c"));
    });
    SP.oak = spr(22, 28, p => {
      p(9, 18, 4, 10, "#5a3a1e"); p(10, 18, 1, 10, "#7a5330");
      rowsShape(p, [[6, 10], [3, 16], [1, 20], [0, 22], [0, 22], [0, 22], [0, 22], [1, 20], [1, 20], [2, 18], [3, 16], [5, 12], [7, 8]], 0, 2, "#1d4a22", "#3f9a3a", "#6cc35a");
      [[5, 6], [14, 5], [9, 9], [16, 10], [4, 11]].forEach(([x, y]) => p(x, y, 2, 2, "#d94a3a"));
    });
    SP.stump = spr(14, 10, p => { p(2, 3, 10, 6, "#4a2f18"); p(3, 2, 8, 3, "#b88a55"); p(5, 3, 4, 1, "#8a6238"); p(3, 8, 8, 2, "rgba(0,0,0,.25)"); });
    const rock = (ore) => spr(16, 14, p => {
      rowsShape(p, [[5, 6], [3, 10], [2, 12], [1, 14], [1, 14], [1, 14], [1, 14], [1, 14], [2, 12], [3, 10]], 0, 2, "#2e3036", ore ? "#6f6c7a" : "#7d8089", ore ? "#8f8c99" : "#a7aab2");
      if (ore) [[4, 6], [9, 5], [11, 9], [5, 9], [8, 8]].forEach(([x, y]) => { p(x, y, 2, 2, "#e8913c"); p(x, y, 1, 1, "#ffd28a"); });
      p(2, 12, 12, 2, "rgba(0,0,0,.22)");
    });
    SP.rock = rock(false); SP.ore = rock(true);
    SP.rubble = spr(16, 10, p => { [[2, 5], [7, 3], [11, 6], [5, 7]].forEach(([x, y]) => { p(x, y, 3, 2, "#6c6f77"); p(x, y, 1, 1, "#a7aab2"); }); });
    SP.table = spr(16, 16, p => {
      p(2, 14, 12, 2, "rgba(0,0,0,.25)"); p(3, 8, 2, 7, "#5a3a1e"); p(11, 8, 2, 7, "#5a3a1e");
      p(1, 5, 14, 4, "#3a2412"); p(2, 5, 12, 3, "#b98247"); p(2, 5, 12, 1, "#d8a56a");
      p(3, 3, 4, 2, "#8d939b"); p(4, 2, 2, 1, "#c7ccd3"); p(9, 4, 5, 1, "#e2d7bd");
    });
    SP.tent = spr(22, 20, p => {
      p(1, 18, 20, 2, "rgba(0,0,0,.25)");
      for (let i = 0; i < 16; i++) { const w = 2 + i * 1.2 | 0, x = 11 - (w >> 1); p(x - 1, 2 + i, w + 2, 1, "#2c3320"); p(x, 2 + i, w, 1, i % 4 < 2 ? "#7e8f52" : "#93a466"); }
      p(9, 10, 4, 8, "#2b2416"); p(10, 1, 1, 3, "#5a3a1e");
    });
    SP.tentFrame = spr(22, 20, p => { for (let i = 0; i < 16; i++) { p(11 - (i * .6 | 0), 2 + i, 1, 1, "#7a5330"); p(11 + (i * .6 | 0), 2 + i, 1, 1, "#7a5330"); } p(3, 17, 16, 1, "#7a5330"); });
    const person = (pal, dir, step) => spr(12, 18, p => {
      const { skin, hair, shirt, pants, boot, out } = pal, l = step ? 1 : 0;
      p(2, 16, 8, 2, "rgba(0,0,0,.25)");
      p(3, 12 + (dir === "side" ? 0 : l), 2, 4 - (dir === "side" ? 0 : l), pants); p(7, 12 + (dir === "side" ? 0 : 1 - l), 2, 4 - (dir === "side" ? 0 : 1 - l), pants);
      if (dir === "side") { p(3 + l * 2, 12, 2, 4, pants); p(7 - l * 2, 12, 2, 4, pants); }
      p(3, 15, 2, 1, boot); p(7, 15, 2, 1, boot);
      p(2, 7, 8, 6, out); p(3, 7, 6, 5, shirt); p(3, 11, 6, 1, "#00000033");
      p(2, 1, 8, 7, out); p(3, 2, 6, 5, skin);
      if (dir === "up") p(3, 1, 6, 5, hair); else { p(3, 1, 6, 2, hair); if (dir === "side") { p(3, 3, 2, 2, hair); p(7, 4, 1, 1, out); } else { p(4, 4, 1, 1, out); p(7, 4, 1, 1, out); } }
      if (pal.zombie) { if (dir !== "up") { p(dir === "side" ? 7 : 4, 4, 1, 1, "#ff3b30"); if (dir !== "side") p(7, 4, 1, 1, "#ff3b30"); } p(4, 6, 4, 1, "#3d5a2c"); }
    });
    const hero = { skin: "#f0c08a", hair: "#7a3e1d", shirt: "#3d6fd6", pants: "#2f3b59", boot: "#2a1d14", out: "#1b1b22" };
    const zed = { skin: "#86b86c", hair: "#3f5a2e", shirt: "#566e3f", pants: "#3b3f55", boot: "#22252e", out: "#162016", zombie: true };
    ["down", "up", "side"].forEach(d => [0, 1].forEach(s => { SP[`p_${d}_${s}`] = person(hero, d, s); SP[`z_${d}_${s}`] = person(zed, d, s); }));
    const icon = f => spr(14, 14, f);
    SP.i_wood = icon(p => { p(1, 5, 11, 5, "#6b4423"); p(1, 5, 11, 1, "#8a5a2f"); p(10, 4, 4, 7, "#c99a5e"); p(11, 6, 2, 3, "#8a6238"); });
    SP.i_stone = icon(p => { rowsShape(p, [[4, 5], [2, 9], [1, 11], [1, 11], [1, 11], [2, 9], [4, 5]], 1, 3, "#2e3036", "#7d8089", "#b5b8bf"); });
    SP.i_iron = icon(p => { p(2, 6, 10, 5, "#3a3d44"); p(3, 6, 8, 4, "#c9ced6"); p(3, 6, 8, 1, "#eef1f5"); p(4, 9, 6, 1, "#9aa1ab"); });
    SP.i_table = icon(p => { p(1, 4, 12, 3, "#b98247"); p(1, 4, 12, 1, "#d8a56a"); p(2, 7, 2, 6, "#5a3a1e"); p(10, 7, 2, 6, "#5a3a1e"); });
    SP.i_pickaxe = icon(p => { for (let i = 0; i < 10; i++) p(3 + i, 12 - i, 2, 2, "#7a5330"); p(5, 1, 8, 2, "#9aa1ab"); p(4, 2, 2, 3, "#9aa1ab"); p(12, 2, 2, 3, "#9aa1ab"); });
    SP.i_sword = icon(p => { for (let i = 0; i < 9; i++) p(4 + i, 9 - i, 2, 2, "#d7dde5"); p(2, 9, 6, 2, "#7a5330"); p(3, 11, 2, 2, "#5a3a1e"); p(12, 1, 1, 1, "#fff"); });
  }

  // ---------- helpers ----------
  const lerp = (a, b, k) => a + (b - a) * k;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  function rng(seed) { let a = (seed >>> 0) || 1; return () => { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
  function rr(g, x, y, w, h, r) { g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r); g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath(); }
  function txt(g, s, x, y, size, col, align, weight) { g.font = `${weight || 700} ${size}px system-ui,Segoe UI,sans-serif`; g.textAlign = align || "left";
    g.lineWidth = 3; g.strokeStyle = "rgba(0,0,0,.6)"; g.strokeText(s, x, y); g.fillStyle = col; g.fillText(s, x, y); g.textAlign = "left"; g.lineWidth = 1; }
  const ITEMS = ["wood", "stone", "iron", "table", "pickaxe", "sword"];

  function modeInfo(mode, ctx, cached, latency) {
    let m;
    if (mode === "human") m = { t: "HUMAN PLAYER · no AI, no API calls", c: "#c9a7ff" };
    else if (mode === "mock") m = { t: "MOCK · simulated agent, no model called", c: "#ffcf66" };
    else if (mode === "real") m = { t: "REAL GEMINI · " + (cached ? "cached reply" : "LIVE call" + (latency ? " " + latency + " ms" : "")), c: "#6fe3a1" };
    else m = { t: ctx === "human" ? "HUMAN PLAYER" : ctx === "replay" ? "REPLAY" : "IDLE", c: "#aab4c2" };
    if (ctx === "replay" && mode) m = { t: "REPLAY · " + m.t, c: "#9fc2ff" };
    return m;
  }

  // ---------- the view ----------
  class GameView {
    constructor(canvas, opts) {
      if (!SP.pine) build();
      this.cv = canvas; canvas.width = VW; canvas.height = VH; this.g = canvas.getContext("2d");
      this.context = (opts && opts.context) || "live"; this.onShow = (opts && opts.onShow) || (() => {});
      this.queue = []; this.anim = null; this.paused = false; this.stepping = false; this.speed = 1; this.follow = true; this.skipped = 0;
      this.E = null;          // the episode being shown: static map + meta
      this.D = null;          // displayed world (always a copy of engine data)
      this.S = null;          // displayed status (step, inventory, ...), from the engine
      this.cur = null;        // the engine frame currently on screen
      this.cam = { x: 12 * TS, y: 7 * TS }; this.parts = []; this.floats = []; this.card = null; this.t = 0;
      this.pp = { x: 12, y: 7 }; this.zp = null; this.face = "down"; this.zface = "down"; this.walkPh = 0; this.act = null; this.flash = 0;
      this._last = performance.now();
      const loop = now => { const dt = Math.min(0.1, (now - this._last) / 1000); this._last = now; this.update(dt); this.draw(); requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    }
    // ----- input: backend events -----
    push(e) {
      const t = e.type;
      if (t === "episode_start" || t === "human_start") this.queue.push({ kind: "start", ev: e });
      else if (t === "step" || t === "human_step") this.queue.push({ kind: "step", ev: e });
      else if (t === "episode_end") this.queue.push({ kind: "end", ev: e });
      else if (t === "proposal" && e.lesson) this.queue.push({ kind: "note", tone: "idea", title: (e.side ? "Side test: " : "") + "Lesson written from the mistakes", sub: `“${e.lesson}”`, who: e.model });
      else if (t === "decision") this.queue.push({ kind: "note", tone: e.accepted ? "ok" : "bad",
        title: e.accepted ? (e.side ? "Gate ACCEPTED (side test, not applied)" : "Gate ACCEPTED: the agent now plays with harness " + e.child) : "Gate REJECTED the change: the agent keeps harness " + (e.current || ""), sub: e.reason });
      else if (t === "rollback") this.queue.push({ kind: "note", tone: "warn", title: "ROLLED BACK to harness " + e.restored, sub: `version ${e.version} did worse on fresh worlds (drop ${(+e.drop).toFixed(3)})` });
      else return;
      this.catchUp();
    }
    catchUp() {
      if (!this.follow || this.context === "replay" || this.queue.length <= 24) return;
      let last = -1; this.queue.forEach((f, i) => { if (f.kind === "start") last = i; });
      const drop = this.queue.splice(0, last > 0 ? last : this.queue.length - 6);
      this.finishAnim(); drop.forEach(f => this.applyInstant(f));
      this.skipped += drop.filter(f => f.kind === "step").length;
      this.queue.unshift(...drop.filter(f => f.kind === "note"));   // learning moments are never skipped
    }
    busy() { return !!this.anim || this.queue.length > 0; }
    setSpeed(s) { this.speed = s; }
    pause() { this.paused = true; } play() { this.paused = false; this.stepping = false; } stepOnce() { this.paused = true; this.stepping = true; }
    reset() { this.queue = []; this.anim = null; this.E = this.D = this.S = this.cur = null; this.card = null; this.skipped = 0; this.emit("reset"); }
    fastForward() { const q = this.queue.splice(0); this.finishAnim(); q.forEach(f => this.applyInstant(f)); this.card = null; this.emit("result"); }
    skipGame(toMoment) {
      this.finishAnim();
      while (this.queue.length && this.queue[0].kind !== "note" && (toMoment || this.queue[0].kind !== "start")) {
        const f = this.queue.shift(); this.applyInstant(f); if (f.kind === "step") this.skipped++; }
      this.card = null; this.emit("result");
    }
    shown() { const S = this.S, D = this.D; if (!S || !D) return null;
      return JSON.parse(JSON.stringify({ seed: S.seed, step: S.step, tick: D.tick, player: D.player, inventory: S.inventory, table: D.table,
        shelter: D.shelter, zombie: D.zombie, outcome: S.outcome, episode: S.episode })); }
    frameShown() { return this.cur ? JSON.parse(JSON.stringify(this.cur)) : null; }

    // ----- frames -----
    startEpisode(e) {
      const st = e.static || { w: 26, h: 15, walls: [], water: [], start: [12, 7] };
      this.E = { st, seed: e.seed, task: e.task, mode: e.mode, suite: e.suite, label: e.label, version: e.version, hazard: e.hazard, max_steps: e.max_steps, episode: e.episode };
      this.D = JSON.parse(JSON.stringify(e.world || {}));
      this.S = { mode: e.mode, suite: e.suite, label: e.label, version: e.version, seed: e.seed, task: e.task, max_steps: e.max_steps, step: e.step || 0,
        inventory: Object.assign({}, e.inventory || {}), outcome: "", episode: e.episode, needed: (e.world || {}).needed || [] };
      this.ground = this.buildGround(st, e.seed); this.cur = null; this.parts = []; this.floats = [];
      const p = this.D.player || st.start; this.pp = { x: p[0], y: p[1] }; this.zp = null; this.face = "down";
      this.cam.x = (this.pp.x + .5) * TS; this.cam.y = (this.pp.y + .5) * TS;
    }
    begin(f) {
      if (f.kind === "start") { this.startEpisode(f.ev); this.card = { kind: "start", life: 1.8 }; this.anim = { f, hold: 1.6, t: 0 }; this.emit("start"); return; }
      if (f.kind === "end") { if (this.S) Object.assign(this.S, { done: true, success: f.ev.success }); this.card = { kind: "end", ev: f.ev, life: 2.4 }; this.anim = { f, hold: 2.2, t: 0 }; this.emit("result"); return; }
      if (f.kind === "note") { this.card = { kind: "note", f, life: 3.0 }; this.anim = { f, hold: 2.8, t: 0 }; return; }
      // a decision: play the engine's frames one by one
      const ev = f.ev; if (!this.E || this.E.seed !== ev.seed) this.startEpisode(Object.assign({}, ev, { world: ev.world, step: (ev.step || 1) - 1 }));
      this.S.decision = { raw: ev.raw, action: ev.action, mode: ev.mode, cached: ev.cached, latency: ev.latency_ms, call: ev.call_id };
      this.S.step = ev.step; this.S.mode = ev.mode;
      this.anim = { f, frames: ev.trace || [], i: -1, t: 0, dur: 0, age: 0, from: { x: this.pp.x, y: this.pp.y }, zfrom: this.zp && { ...this.zp } };
      this.emit("decide", ev);
      this.nextFrame();
    }
    tickDur(a, z) { const base = 1 / 7; return a === "walk" ? base : a === "hide" ? (z ? base : base * .3) : a === "think" ? base * 4 : base * 3.2; }
    nextFrame() {
      const A = this.anim; A.i++; A.t = 0;
      if (A.i >= A.frames.length) return this.endStep();
      const fr = A.frames[A.i], prev = this.cur;
      A.from = { x: this.pp.x, y: this.pp.y }; A.zfrom = this.zp ? { ...this.zp } : (fr.z ? { x: fr.z[0], y: fr.z[1] } : null);
      A.dur = this.tickDur(fr.a, fr.z);
      this.cur = fr;
      // engine-recorded state for this tick
      const D = this.D; D.tick = fr.t; D.table = fr.table; D.shelter = fr.shelter; D.zombie = fr.z; this.S.inventory = Object.assign({}, fr.inv);
      if (fr.a === "gather" && fr.tg && fr.left != null) {
        const list = D.trees.find(q => q[0] === fr.tg[0] && q[1] === fr.tg[1]) ? D.trees : D.stones.find(q => q[0] === fr.tg[0] && q[1] === fr.tg[1]) ? D.stones : D.ores;
        const r = list.find(q => q[0] === fr.tg[0] && q[1] === fr.tg[1]); if (r) r[2] = fr.left;
      }
      const dx = fr.p[0] - this.pp.x, dy = fr.p[1] - this.pp.y;
      if (dx || dy) this.face = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up");
      else if (fr.tg) { const ex = fr.tg[0] - fr.p[0], ey = fr.tg[1] - fr.p[1]; this.face = Math.abs(ex) > Math.abs(ey) ? (ex > 0 ? "right" : "left") : (ey > 0 ? "down" : "up"); }
      this.effects(fr);
    }
    effects(fr) {
      const at = (p) => ({ x: (p[0] + .5) * TS, y: (p[1] + .3) * TS });
      const me = at(fr.p);
      if (fr.a === "gather") { const k = (fr.n || "").split(" ").pop(); this.burst(at(fr.tg), k); this.float(me, fr.n, "#fff4a8"); }
      if (fr.a === "slip") this.float(me, "slipped!", "#e8e8e8");
      if (fr.a === "craft") { this.burst(at(fr.tg), "wood"); this.float(me, fr.n, "#c7f5c4"); }
      if (fr.a === "build" && fr.n) this.float(me, "shelter built", "#c7f5c4");
      if (fr.a === "fail" || fr.a === "think") { const out = this.anim.f.ev.outcome || ""; if (/failed:/.test(out) && this.anim.i === this.anim.frames.length - 1)
        this.float(me, "✗ " + out.split("failed:")[1].replace(/\.$/, "").trim().slice(0, 48), "#ff7b6e"); }
      if (fr.ze === "zombie_appears") { this.zp = { x: fr.z[0], y: fr.z[1] }; this.float(at(fr.z), "a zombie appears!", "#ffb86b"); this.burst(at(fr.z), "smoke"); }
      if (fr.ze === "zombie_attack") { this.flash = .6; this.float(me, "zombie stole all your wood!", "#ff7b6e"); this.burst(me, "wood"); }
      if (fr.ze === "table_wrecked") { this.float(at(fr.p), "your table was wrecked", "#ff7b6e"); if (this.lastTable) this.burst(at(this.lastTable), "wood"); }
      if (fr.ze === "zombie_blocked") this.float(me, "blocked by the shelter", "#c7f5c4");
      if (fr.ze === "zombie_gives_up" || fr.ze === "zombie_leaves" || fr.ze === "zombie_leaves_dawn") { if (this.zp) this.burst({ x: (this.zp.x + .5) * TS, y: (this.zp.y + .3) * TS }, "smoke"); this.float(me, fr.ze === "zombie_gives_up" ? "the zombie gave up" : "the zombie left", "#c7f5c4"); }
      if (fr.ze === "zombie_slain") { this.float(me, "zombie slain with the sword!", "#c7f5c4"); if (this.zp) this.burst({ x: (this.zp.x + .5) * TS, y: (this.zp.y + .3) * TS }, "smoke"); }
      this.lastTable = fr.table || this.lastTable;
    }
    endStep() {   // THE sync point: everything shown becomes the engine's snapshot after the step
      const ev = this.anim.f.ev;
      this.D = JSON.parse(JSON.stringify(ev.world)); this.D.tick = ev.world.tick;
      this.pp = { x: ev.world.player[0], y: ev.world.player[1] }; this.zp = ev.world.zombie ? { x: ev.world.zombie[0], y: ev.world.zombie[1] } : null;
      Object.assign(this.S, { step: ev.step, inventory: Object.assign({}, ev.inventory), outcome: ev.outcome, plan: ev.plan, needed: ev.world.needed || [] });
      this.anim = { f: this.anim.f, hold: .35, t: 0, after: true };
      this.emit("result", ev);
    }
    finishAnim() { const A = this.anim; if (A && A.f.kind === "step" && !A.after) { this.endStep(); } this.anim = null; }
    applyInstant(f) {
      if (f.kind === "start") { this.startEpisode(f.ev); }
      else if (f.kind === "step") { if (!this.E || this.E.seed !== f.ev.seed) this.startEpisode(Object.assign({}, f.ev, { step: f.ev.step - 1 }));
        this.anim = { f, frames: [], i: 0 }; this.endStep(); this.anim = null; }
      else if (f.kind === "end" && this.S) Object.assign(this.S, { done: true, success: f.ev.success });
    }
    emit(phase, ev) { try { this.onShow(this.S, ev || null, phase, this); } catch (_) {} }
    float(at, text, col) {   // stack labels so two events never print on top of each other
      let y = at.y - 18; while (this.floats.some(f => Math.abs(f.x - at.x) < 140 && Math.abs(f.y - y) < 18)) y -= 20;
      this.floats.push({ x: at.x, y, text, col, life: 1.9 }); }
    burst(at, kind) { const c = { wood: "#c99a5e", stone: "#a7aab2", iron: "#e8913c", smoke: "#cfd6cf" }[kind] || "#e9e2c8";
      for (let i = 0; i < 16; i++) this.parts.push({ x: at.x, y: at.y, vx: (Math.random() - .5) * 170, vy: -Math.random() * 170, life: .6 + Math.random() * .4, c, s: kind === "smoke" ? 6 : 4 }); }

    // ----- time -----
    update(dt) {
      this.t += dt; const sp = this.speed;
      this.parts = this.parts.filter(p => (p.life -= dt) > 0); this.parts.forEach(p => { p.x += p.vx * dt; p.y += p.vy * dt; p.vy += 380 * dt; });
      this.floats = this.floats.filter(f => (f.life -= dt) > 0); this.floats.forEach(f => f.y -= 26 * dt);
      if (this.card && (this.card.life -= dt * sp) <= 0) this.card = null;
      this.flash = Math.max(0, this.flash - dt);
      // camera follows the engine position of the player
      let tx = (this.pp.x + .5) * TS, ty = (this.pp.y + .5) * TS; const k = 1 - Math.pow(.02, dt);
      if (this.zp) { tx = lerp(tx, (this.zp.x + .5) * TS, .3); ty = lerp(ty, (this.zp.y + .5) * TS, .3); }   // keep the danger in view
      this.cam.x = lerp(this.cam.x, tx, k); this.cam.y = lerp(this.cam.y, ty, k);
      if (!this.anim) { if (this.paused && !this.stepping) return; const f = this.queue.shift(); if (f) this.begin(f); return; }
      if (this.paused && !this.stepping) return;
      const A = this.anim; A.t += dt * sp; if (A.age != null) A.age += dt * sp;
      if (A.hold != null) { if (A.t >= A.hold) { const wasStep = A.f.kind === "step"; this.anim = null; if (this.stepping && wasStep) this.stepping = false; } return; }
      const fr = A.frames[A.i]; const k2 = clamp(A.t / A.dur, 0, 1);
      if (fr) {
        this.pp = { x: lerp(A.from.x, fr.p[0], k2), y: lerp(A.from.y, fr.p[1], k2) };
        if (fr.z) { const zf = A.zfrom || { x: fr.z[0], y: fr.z[1] }; this.zp = { x: lerp(zf.x, fr.z[0], k2), y: lerp(zf.y, fr.z[1], k2) };
          const zx = fr.z[0] - zf.x, zy = fr.z[1] - zf.y; if (zx || zy) this.zface = Math.abs(zx) > Math.abs(zy) ? (zx > 0 ? "right" : "left") : (zy > 0 ? "down" : "up"); }
        else if (!fr.z && fr.ze !== "zombie_appears") this.zp = null;
        if (fr.a === "walk") this.walkPh += dt * sp * 9;
      }
      if (A.t >= A.dur) { if (fr) this.pp = { x: fr.p[0], y: fr.p[1] }; this.nextFrame(); }
    }

    // ----- drawing -----
    buildGround(st, seed) {
      const W = st.w, H = st.h, c = document.createElement("canvas"); c.width = W * U; c.height = H * U; const g = c.getContext("2d"), r = rng(seed * 31 + 3);
      const walls = new Set(st.walls.map(p => p + "")), water = new Set(st.water.map(p => p + ""));
      for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
        const mine = x > 19 && y < 6, camp = Math.hypot(x - st.start[0], y - st.start[1]) < 2.6, key = [x, y] + "";
        const base = mine ? ["#4f4c58", "#56535f"] : camp ? ["#a17c4c", "#93703f"] : ["#5e9e48", "#68a952"];
        g.fillStyle = base[(x + y) % 2 ? 1 : 0]; g.fillRect(x * U, y * U, U, U);
        for (let i = 0; i < 7; i++) { g.fillStyle = mine ? "#45424d" : camp ? "#866438" : (r() < .5 ? "#548f40" : "#74b65c"); g.fillRect(x * U + (r() * 15 | 0), y * U + (r() * 15 | 0), 1 + (r() * 2 | 0), 1); }
        if (!mine && !camp && r() < .06) { g.fillStyle = r() < .5 ? "#f1e36b" : "#f4f4f4"; g.fillRect(x * U + 5 + (r() * 6 | 0), y * U + 5 + (r() * 6 | 0), 2, 2); }
        if (!mine && !camp && r() < .08) { g.fillStyle = "#3f7d33"; [[3, 8], [5, 6], [7, 9]].forEach(([a, b]) => g.fillRect(x * U + a + 3, y * U + b, 1, 4)); }
        if (water.has(key)) { g.fillStyle = "#3a83c0"; g.fillRect(x * U, y * U, U, U); g.fillStyle = "#4b97d3"; g.fillRect(x * U + 2, y * U + 4, 10, 1); if (!water.has([x, y - 1] + "")) { g.fillStyle = "#cbe7f7"; g.fillRect(x * U, y * U, U, 2); } }
        if (walls.has(key)) { g.fillStyle = "#3b3842"; g.fillRect(x * U, y * U, U, U); g.fillStyle = "#6e6a78"; g.fillRect(x * U, y * U, U, 5); g.fillStyle = "#57535f";
          g.fillRect(x * U + 1, y * U + 7, 6, 3); g.fillRect(x * U + 9, y * U + 7, 6, 3); g.fillRect(x * U + 4, y * U + 11, 7, 3); }
      }
      if (st.entrance) { const [ex, ey] = st.entrance; g.fillStyle = "#2a2730"; g.fillRect(ex * U + 2, ey * U + 2, U - 4, U - 4); }
      return c;
    }
    draw() {
      const g = this.g; g.imageSmoothingEnabled = false;
      g.fillStyle = "#1b2330"; g.fillRect(0, 0, VW, VH);
      if (!this.E || !this.D) { this.drawHud(); return; }
      const st = this.E.st, ww = st.w * TS, wh = st.h * TS;
      let cx = clamp(this.cam.x - VW / 2, 0, Math.max(0, ww - VW)), cy = clamp(this.cam.y - VH / 2 + 30, -40, Math.max(0, wh - VH + 60));
      if (this.flash) { cx += (Math.random() - .5) * 10 * this.flash; cy += (Math.random() - .5) * 10 * this.flash; }
      g.save(); g.translate(-Math.round(cx), -Math.round(cy));
      g.drawImage(this.ground, 0, 0, ww, wh);
      g.globalAlpha = .7; txt(g, "FOREST", 4.5 * TS, .62 * TS, 13, "#e9f5d8", "center", 800); txt(g, "QUARRY", 12 * TS, 9.55 * TS, 13, "#f0f0f0", "center", 800);
      txt(g, "MINE (iron)", 22.6 * TS, .62 * TS, 13, "#e6e1f5", "center", 800); txt(g, "CAMP", (st.start[0] + .5) * TS, (st.start[1] + 2.7) * TS, 12, "#f6e7c8", "center", 800); g.globalAlpha = 1;
      // water shimmer
      g.fillStyle = "rgba(255,255,255,.18)"; st.water.forEach(([x, y], i) => g.fillRect(x * TS + 6 + ((this.t * 14 + i * 9) % 30), y * TS + 18 + i % 3 * 8, 10, 3));
      const D = this.D, objs = [], put = (y, f) => objs.push({ y, f });
      const sprAt = (s, x, y, ax, ay) => g.drawImage(s, Math.round(x - s.width * Z * ax), Math.round(y - s.height * Z * ay), s.width * Z, s.height * Z);
      const behind = (x, y, q) => q && Math.abs(q.x - x) < 1 && q.y < y + .5 && q.y > y - 1.6;
      (D.trees || []).forEach(([x, y, n]) => put(y + .9, () => { const r = (x * 7 + y * 13) % 5;
        if (n > 0 && (behind(x, y, this.pp) || behind(x, y, this.zp))) g.globalAlpha = .45;
        sprAt(n > 0 ? (r === 0 ? SP.oak : r === 1 ? SP.pine2 : SP.pine) : SP.stump, (x + .5) * TS, (y + 1) * TS - 4, .5, 1); g.globalAlpha = 1;
        if (n > 0) this.pips(x, y, n, 2, "#c99a5e"); }));
      (D.stones || []).forEach(([x, y, n]) => put(y + .8, () => { sprAt(n > 0 ? SP.rock : SP.rubble, (x + .5) * TS, (y + 1) * TS - 2, .5, 1); if (n > 0) this.pips(x, y, n, 2, "#c7cbd3"); }));
      (D.ores || []).forEach(([x, y, n]) => put(y + .8, () => { sprAt(n > 0 ? SP.ore : SP.rubble, (x + .5) * TS, (y + 1) * TS - 2, .5, 1); }));
      if (D.table) put(D.table[1] + .8, () => sprAt(SP.table, (D.table[0] + .5) * TS, (D.table[1] + 1) * TS - 2, .5, 1));
      const inTent = D.shelter && this.pp && Math.abs(this.pp.x - D.shelter[0]) < .01 && Math.abs(this.pp.y - D.shelter[1]) < .01;
      const fr = this.cur, building = fr && fr.a === "build" && !fr.n;
      if (D.shelter) put(D.shelter[1] + .95, () => { sprAt(building ? SP.tentFrame : SP.tent, (D.shelter[0] + .5) * TS, (D.shelter[1] + 1) * TS + 2, .5, 1);
        if (inTent && !building) { g.fillStyle = "rgba(255,230,140,.35)"; g.fillRect((D.shelter[0] + .5) * TS - 6, (D.shelter[1] + .55) * TS, 12, 18); } });
      // player (hidden inside the tent while sheltered)
      if (!inTent || building) put(this.pp.y + .95, () => this.drawPerson("p", this.pp, this.face, fr));
      if (this.zp) put(this.zp.y + .95, () => this.drawPerson("z", this.zp, this.zface, fr));
      objs.sort((a, b) => a.y - b.y).forEach(o => o.f());
      this.parts.forEach(p => { g.fillStyle = p.c; g.fillRect(p.x, p.y, p.s, p.s); });
      this.floats.forEach(f => { g.globalAlpha = Math.min(1, f.life); txt(g, f.text, f.x, f.y, 15, f.col, "center"); g.globalAlpha = 1; });
      if (this.anim && this.anim.f.kind === "step" && !this.anim.after && this.anim.age < 1.5) { g.globalAlpha = clamp((1.5 - this.anim.age) * 3, 0, 1); this.drawBubble(this.anim.f.ev); g.globalAlpha = 1; }
      g.restore();
      if (this.zp) {   // off-screen zombie: an edge marker pointing at its engine position
        const zx = (this.zp.x + .5) * TS - Math.round(cx), zy = (this.zp.y + .5) * TS - Math.round(cy);
        if (zx < 0 || zy < 0 || zx > VW || zy > VH) {
          const ex = clamp(zx, 40, VW - 40), ey = clamp(zy, 90, VH - 90), a = Math.atan2(zy - ey, zx - ex);
          const d = Math.abs(Math.round(this.zp.x) - Math.round(this.pp.x)) + Math.abs(Math.round(this.zp.y) - Math.round(this.pp.y));
          g.save(); g.translate(ex, ey); g.fillStyle = "rgba(14,17,22,.8)"; g.beginPath(); g.arc(0, 0, 24, 0, 7); g.fill();
          g.rotate(a); g.fillStyle = "#ff6b5e"; g.beginPath(); g.moveTo(22, 0); g.lineTo(8, -9); g.lineTo(8, 9); g.closePath(); g.fill(); g.restore();
          txt(g, "zombie", ex, ey - 2, 11, "#ff8b80", "center"); txt(g, d + " tiles", ex, ey + 12, 11, "#ffd0cc", "center", 600);
        }
      }
      if (this.flash) { g.fillStyle = `rgba(200,30,30,${this.flash * .35})`; g.fillRect(0, 0, VW, VH); }
      this.drawHud();
      if (this.card) this.drawCard();
    }
    pips(x, y, n, max, col) { const g = this.g; for (let i = 0; i < max; i++) { g.fillStyle = i < n ? col : "rgba(0,0,0,.35)"; g.fillRect((x + .5) * TS - 8 + i * 9, (y + 1) * TS - 2, 6, 4); } }
    drawPerson(who, pos, face, fr) {
      const g = this.g, step = (who === "p" ? (this.anim && this.cur && this.cur.a === "walk") : true) ? (Math.floor((who === "p" ? this.walkPh : this.t * 6)) % 2) : 0;
      const dir = face === "left" || face === "right" ? "side" : face, s = SP[`${who}_${dir}_${step}`];
      const x = (pos.x + .5) * TS, y = (pos.y + 1) * TS - 2, flip = face === "left";
      g.save(); g.translate(Math.round(x), Math.round(y)); if (flip) g.scale(-1, 1);
      g.drawImage(s, -s.width * Z / 2, -s.height * Z, s.width * Z, s.height * Z);
      if (who === "p" && this.S) {   // tool in hand: what the engine says the player holds / is using
        const inv = this.S.inventory || {}, acting = fr && (fr.a === "gather" || fr.a === "craft" || fr.a === "slip" || fr.a === "fail");
        const sw = acting && this.anim && this.anim.dur ? Math.sin(clamp(this.anim.t / this.anim.dur, 0, 1) * Math.PI * 2) : 0;
        const icon = inv.sword ? SP.i_sword : (acting || inv.pickaxe) ? (inv.pickaxe ? SP.i_pickaxe : null) : null;
        if (icon) { g.save(); g.translate(10, -26); g.rotate(-.4 + sw * 1.1); g.drawImage(icon, -6, -30, 28, 28); g.restore(); }
      }
      if (who === "z" && this.cur && /zombie_attack|zombie_blocked/.test(this.cur.ze || "")) { g.fillStyle = "#ff3b30"; g.font = "bold 22px system-ui"; g.fillText("!", -4, -s.height * Z - 6); }
      g.restore();
    }
    drawBubble(ev) {
      const g = this.g, human = ev.mode === "human", who = human ? "You" : ev.mode === "mock" ? "Mock agent" : "Gemini";
      const raw = String(ev.raw == null ? "" : ev.raw).replace(/\s+/g, " ").trim().slice(0, 34);
      const line = human ? `${who}: ${ev.action}` : `${who}: "${raw}"` + (raw.toLowerCase() === ev.action ? "" : ` → ${ev.action}`);
      g.font = "700 15px system-ui,Segoe UI,sans-serif"; const w = g.measureText(line).width + 22;
      const x = (this.pp.x + .5) * TS - w / 2, y = (this.pp.y - .25) * TS - 64;
      g.fillStyle = "rgba(255,255,255,.96)"; rr(g, x, y, w, 30, 9); g.fill();
      g.strokeStyle = human ? "#8e5bd8" : ev.mode === "mock" ? "#b7791f" : "#1a7f4b"; g.lineWidth = 2; g.stroke(); g.lineWidth = 1;
      g.beginPath(); g.moveTo(x + w / 2 - 6, y + 30); g.lineTo(x + w / 2, y + 38); g.lineTo(x + w / 2 + 6, y + 30); g.fillStyle = "rgba(255,255,255,.96)"; g.fill();
      g.fillStyle = "#1c2128"; g.fillText(line, x + 11, y + 20);
    }
    drawHud() {
      const g = this.g, S = this.S, D = this.D, dec = S && S.decision;
      const m = modeInfo(S && S.mode, this.context, dec && dec.cached, dec && dec.latency);
      g.font = "800 13px system-ui,Segoe UI,sans-serif"; const mw = g.measureText(m.t).width + 22;
      g.fillStyle = "rgba(14,17,22,.78)"; rr(g, 10, 10, mw + 6, 30, 15); g.fill(); g.fillStyle = m.c; rr(g, 13, 13, mw, 24, 12); g.fill(); g.fillStyle = "#10141a"; g.fillText(m.t, 24, 30);
      if (!S || !D) { txt(g, "Press “Start a new game” to watch the AI, or open “Play yourself”.", VW / 2, VH / 2, 18, "#e6eaf0", "center"); return; }
      // objective panel (top right)
      const need = S.needed || [], py = 10, ph = 46 + Math.max(1, need.length) * 18;
      g.font = "600 12px system-ui,Segoe UI,sans-serif";
      const pw = Math.max(250, ...need.slice(0, 4).map(n => g.measureText("• still needed: " + n).width + 26)), px = VW - pw - 10;
      g.fillStyle = "rgba(14,17,22,.78)"; rr(g, px, py, pw, ph, 10); g.fill();
      txt(g, `OBJECTIVE: craft a ${String(S.task).toUpperCase()}`, px + 12, py + 22, 14, "#ffe08a");
      if (S.success || S.inventory && S.inventory[S.task]) txt(g, "done!", px + 12, py + 42, 13, "#6fe3a1");
      else (need.length ? need : ["…"]).slice(0, 4).forEach((n, i) => txt(g, "• still needed: " + n, px + 12, py + 42 + i * 18, 12, "#e6eaf0", "left", 600));
      // decision / time strip (top center-left)
      const info = `${String(S.suite || "").toUpperCase()} world ${S.seed} · decision ${S.step || 0}/${S.max_steps || 14} · tick ${D.tick || 0}` + (S.mode === "human" ? "" : ` · harness ${S.version || "?"}${S.label ? " (" + S.label + ")" : ""}`);
      g.font = "600 13px system-ui,Segoe UI,sans-serif"; g.fillStyle = "rgba(14,17,22,.72)"; rr(g, 10, 45, g.measureText(info).width + 20, 24, 8); g.fill();
      txt(g, info, 20, 62, 13, "#e6eaf0", "left", 600);
      // hotbar (inventory straight from the engine)
      const inv = S.inventory || {}, hx = VW / 2 - 3 * 58, hy = VH - 66;
      g.fillStyle = "rgba(14,17,22,.72)"; rr(g, hx - 8, hy - 8, 6 * 58 + 10, 64, 10); g.fill();
      ITEMS.forEach((k, i) => { const x = hx + i * 58, n = inv[k] || 0; g.fillStyle = n ? "rgba(255,224,138,.16)" : "rgba(255,255,255,.05)"; rr(g, x, hy, 52, 48, 7); g.fill();
        g.strokeStyle = n ? "#f5d76e" : "#3b4554"; g.lineWidth = 2; g.stroke(); g.lineWidth = 1; g.globalAlpha = n ? 1 : .35; g.drawImage(SP["i_" + k], x + 12, hy + 6, 28, 28); g.globalAlpha = 1;
        txt(g, String(n), x + 46, hy + 44, 12, n ? "#fff" : "#6b7685", "right"); txt(g, k, x + 26, hy + 58, 10, "rgba(255,255,255,.7)", "center", 600); });
      // survival status (bottom right)
      let th = { t: "Safe for now", c: "#c7f5c4" };
      if (D.in_shelter || (D.shelter && this.pp && Math.abs(this.pp.x - D.shelter[0]) < .01 && Math.abs(this.pp.y - D.shelter[1]) < .01)) th = { t: "Hiding in the shelter", c: "#c7f5c4" };
      else if (D.zombie) { const d = Math.abs(D.zombie[0] - Math.round(this.pp.x)) + Math.abs(D.zombie[1] - Math.round(this.pp.y)); th = { t: D.zombie[2] === "wreck" ? "Zombie heading for your table!" : `Zombie ${d} tiles away!` + (inv.sword ? " (armed)" : ""), c: "#ff8b80" }; }
      else if (D.zombie_coming) th = { t: "Groaning nearby: a zombie is coming", c: "#ffb86b" };
      g.font = "800 13px system-ui,Segoe UI,sans-serif"; const tw = g.measureText(th.t).width + 24;
      g.fillStyle = "rgba(14,17,22,.8)"; rr(g, VW - tw - 10, VH - 44, tw, 32, 9); g.fill(); txt(g, th.t, VW - tw + 2, VH - 23, 13, th.c);
      // recipes (bottom left): what the engine's recipes are, ticked when possible right now
      const R = (this.E.st.recipes) || {}, rx = 10, ry = VH - 96;
      g.fillStyle = "rgba(14,17,22,.72)"; rr(g, rx, ry, 196, 86, 10); g.fill(); txt(g, "CRAFTING", rx + 10, ry + 18, 11, "#aab4c2");
      Object.entries(R).forEach(([item, need], i) => { const ok = Object.entries(need).every(([k, v]) => (inv[k] || 0) >= v) && (item === "table" || !!D.table);
        txt(g, `${item}: ${Object.entries(need).map(([k, v]) => v + " " + k).join(" + ")}${item === "table" ? "" : " @table"}`, rx + 10, ry + 38 + i * 18, 11, ok ? "#6fe3a1" : "#e6eaf0", "left", 600); });
      if (this.skipped) txt(g, `skipped ${this.skipped} steps` + (this.context === "replay" ? "" : " to stay live"), VW - 12, VH - 54, 11, "rgba(255,255,255,.75)", "right", 600);
      if (this.paused) txt(g, this.stepping ? "STEP" : "PAUSED", VW / 2, 84, 16, "#fff", "center");
    }
    drawCard() {
      const g = this.g, c = this.card, S = this.S || {};
      g.globalAlpha = clamp(c.life * 1.6, 0, 1);
      const w = 620, h = c.kind === "note" ? 120 : 140, x = (VW - w) / 2, y = (VH - h) / 2 - 20;
      g.fillStyle = "rgba(10,12,16,.82)"; rr(g, x, y, w, h, 14); g.fill();
      if (c.kind === "start") {
        txt(g, `${String(S.suite || "").toUpperCase()} WORLD ${S.seed}`, VW / 2, y + 40, 22, "#fff", "center");
        txt(g, `Objective: craft a ${S.task}` + (this.E && this.E.hazard ? " · something lurks in this world…" : ""), VW / 2, y + 74, 16, "#ffe08a", "center");
        txt(g, S.mode === "human" ? "You decide; the Python engine plays it out" : `Played by harness ${S.version}${S.label ? " (" + S.label + ")" : ""}`, VW / 2, y + 104, 14, "#aab4c2", "center", 600);
      } else if (c.kind === "end") {
        const ok = c.ev.success; txt(g, ok ? "SUCCESS" : "FAILED", VW / 2, y + 48, 32, ok ? "#6fe3a1" : "#ff7b6e", "center");
        txt(g, ok ? `crafted the ${S.task} in ${c.ev.steps} decisions` : `no ${S.task} after ${c.ev.steps} decisions`, VW / 2, y + 82, 16, "#e6eaf0", "center");
        const ms = (c.ev.mistakes || []).filter(m => m !== "slip"); txt(g, ms.length ? "mistakes: " + ms.join(", ") : "no avoidable mistakes", VW / 2, y + 110, 13, "#aab4c2", "center", 600);
      } else {
        const col = { ok: "#6fe3a1", bad: "#ff7b6e", warn: "#ffcf66", idea: "#9fc2ff" }[c.f.tone];
        g.strokeStyle = col; g.lineWidth = 2; rr(g, x, y, w, h, 14); g.stroke(); g.lineWidth = 1;
        txt(g, c.f.title, VW / 2, y + 44, 18, col, "center"); txt(g, (c.f.sub || "").slice(0, 92), VW / 2, y + 78, 14, "#e6eaf0", "center", 600);
      }
      g.globalAlpha = 1;
    }
  }
  global.GameView = GameView;
})(window);
