// MiniCraft renderer for Gatekeeper.
//
// THE PYTHON ENGINE IS THE ONLY SOURCE OF GAME STATE. This file draws it; it never decides an
// outcome. Every frame shown comes from one backend event (an AI step, a replayed step, or the
// reply to a human action). The world layout (where trees and rocks are drawn) and the walking
// between them are presentation only: MiniCraft itself has no positions. After each action the
// displayed inventory, table, shelter, step count and result are replaced by the backend's values,
// so the picture cannot drift from the engine. `shown()` exposes exactly what is on screen so
// tests can compare it with the backend event that produced it.
"use strict";
(function (global) {
  const TILE = 32, COLS = 30, ROWS = 17, W = 960, H = 540;
  // Display-only copies of engine constants (gk/env.py): the zombie is announced from CUE_STEP
  // and attacks at ZOMBIE_STEP. They only place the zombie sprite; whether an attack happened is
  // read from the backend's outcome text.
  const CUE_STEP = 3, ZOMBIE_STEP = 5;
  const C = {
    grass1: "#6dbb57", grass2: "#64b04f", grass3: "#74c35e", dirt: "#b58a57", dirt2: "#a67c4b",
    cave: "#5a5866", cave2: "#504e5b", water: "#4aa3df", water2: "#6bb8ea", trunk: "#7a4f2a",
    leaf: "#2f7d32", leaf2: "#3d9440", rock: "#8e9096", rock2: "#a9abb0", ore: "#d8893a",
    skin: "#f1c27d", shirt: "#3b6fd8", pants: "#2d3a55", hair: "#4a2e1a",
    zskin: "#7fb069", zshirt: "#4e6b3a", wood: "#a0662f", plank: "#c48a4a",
  };

  function rng(seed) {   // mulberry32: same world number -> same drawing, every time
    let a = (seed >>> 0) || 1;
    return () => { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }

  function layout(seed) {
    const r = rng(Number(seed) * 2654435761 + 7), used = new Set();
    const pick = (x0, x1, y0, y1, n) => {
      const out = [];
      for (let k = 0; k < 400 && out.length < n; k++) {
        const x = x0 + Math.floor(r() * (x1 - x0 + 1)), y = y0 + Math.floor(r() * (y1 - y0 + 1));
        const key = x + "," + y, near = [...used].some(u => { const [ux, uy] = u.split(",").map(Number); return Math.abs(ux - x) + Math.abs(uy - y) < 2; });
        if (!near) { used.add(key); out.push({ x, y }); }
      }
      return out;
    };
    const shade = [];
    for (let y = 0; y < ROWS; y++) { shade.push([]); for (let x = 0; x < COLS; x++) shade[y].push(r()); }
    return {
      shade,
      trees: pick(1, 8, 2, 13, 10).sort((a, b) => a.y - b.y),
      rocks: pick(9, 17, 12, 15, 6),
      ores: pick(23, 28, 2, 7, 5),
      pond: { x: 20, y: 12, w: 3, h: 3 },
      table: { x: 13, y: 7 }, hut: { x: 17, y: 5 }, spawn: { x: 15, y: 9 }, zspawn: { x: 28.5, y: 13.5 },
    };
  }

  const ITEMS = ["wood", "stone", "iron", "table", "pickaxe", "sword"];

  class GameView {
    constructor(canvas, opts) {
      this.cv = canvas; this.g = canvas.getContext("2d");
      canvas.width = W; canvas.height = H;
      this.context = (opts && opts.context) || "live";   // live | replay | human
      this.onShow = (opts && opts.onShow) || (() => {});
      this.queue = []; this.anim = null; this.paused = false; this.speed = 1; this.follow = true;
      this.skipped = 0; this.S = null; this.L = layout(1); this.p = { x: 15, y: 9, face: 1 };
      this.tries = {}; this.parts = []; this.floats = []; this.toast = null; this.overlay = null;
      this.wiped = false; this.t = 0; this.walk = 0; this.swing = 0; this.lastStepFrame = null;
      this._last = performance.now();
      const loop = now => { const dt = Math.min(0.1, (now - this._last) / 1000); this._last = now;
        this.update(dt); this.draw(); requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    }

    // ---- input: backend events ------------------------------------------------------------
    push(e) {
      const t = e.type;
      if (t === "episode_start") this.queue.push({ kind: "start", ev: e });
      else if (t === "step") this.queue.push({ kind: "step", ev: e });
      else if (t === "episode_end") this.queue.push({ kind: "end", ev: e });
      else if (t === "human_start") this.queue.push({ kind: "start", ev: e });
      else if (t === "human_step") this.queue.push({ kind: "step", ev: e });
      else if (t === "proposal" && e.lesson) this.queue.push({ kind: "note", tone: "idea",
        text: `${e.side ? "Side test — " : ""}${who(e.model)} wrote a lesson from the mistakes:`, sub: `“${e.lesson}”` });
      else if (t === "decision") this.queue.push({ kind: "note", tone: e.accepted ? "ok" : "bad",
        text: (e.accepted ? (e.side ? "Gate ACCEPTED (side test, not applied)" : "Gate ACCEPTED → the agent now plays with harness " + e.child)
          : "Gate REJECTED the change → the agent keeps harness " + (e.current || "")), sub: e.reason });
      else if (t === "rollback") this.queue.push({ kind: "note", tone: "warn",
        text: "ROLLED BACK → harness " + e.restored + " restored", sub: `version ${e.version} regressed on fresh worlds (drop ${(+e.drop).toFixed(3)})` });
      else return;
      this.catchUp();
    }
    catchUp() {   // live only: if the backend is far ahead, jump to the newest episode instead of lagging
      if (!this.follow || this.context === "replay" || this.queue.length <= 30) return;
      let lastStart = -1;
      this.queue.forEach((f, i) => { if (f.kind === "start") lastStart = i; });
      const cut = lastStart > 0 ? lastStart : this.queue.length - 8;
      const drop = this.queue.splice(0, cut);
      drop.forEach(f => this.applyInstant(f));
      this.skipped += drop.filter(f => f.kind === "step").length;
      // never skip the learning moments: lessons, gate decisions and rollbacks are always shown
      this.queue.unshift(...drop.filter(f => f.kind === "note"));
      this.anim = null;
    }
    skipGame(toMoment) {   // jump to the next game (or, with toMoment, the next lesson/decision); skipped steps still apply backend state
      if (this.anim && this.anim.f.kind === "step") { this.apply(this.anim.f.ev, true); }
      this.anim = null;
      while (this.queue.length && this.queue[0].kind !== "note" && (toMoment || this.queue[0].kind !== "start")) {
        const f = this.queue.shift(); this.applyInstant(f); if (f.kind === "step") this.skipped++;
      }
      this.overlay = null; this.emit();
    }
    busy() { return !!this.anim || this.queue.length > 0; }
    fastForward() { const q = this.queue.splice(0); q.forEach(f => this.applyInstant(f)); this.anim = null; this.overlay = null; this.toast = null; this.emit(); }
    setSpeed(s) { this.speed = s; }
    pause() { this.paused = true; }
    play() { this.paused = false; this.stepping = false; }
    stepOnce() { this.paused = true; this.stepping = true; }
    shown() { return this.S ? JSON.parse(JSON.stringify({ seed: this.S.seed, step: this.S.step, inventory: this.S.inventory,
      sheltered: this.S.sheltered, outcome: this.S.outcome, episode: this.S.episode || null })) : null; }

    // ---- frames -----------------------------------------------------------------------------
    begin(f) {
      if (f.kind === "start") {
        const e = f.ev; this.L = layout(e.seed); this.tries = {}; this.wiped = false; this.parts = []; this.floats = [];
        this.p = { x: this.L.spawn.x, y: this.L.spawn.y, face: 1 };
        this.S = snap(e, { step: 0, inventory: {}, sheltered: false, cue: false, outcome: "", raw: "", action: "" });
        this.overlay = { kind: "start", life: 1.6 };
        this.anim = { f, phase: "hold", t: 0, dur: 1.4 };
      } else if (f.kind === "step") {
        const tg = this.target(f.ev.action);
        const dist = Math.hypot(tg.x - this.p.x, tg.y - this.p.y);
        this.p.face = tg.x >= this.p.x ? 1 : -1;
        this.anim = { f, phase: "walk", t: 0, from: { x: this.p.x, y: this.p.y }, to: tg, dur: Math.max(0.15, dist / 7) };
        this.bubble = { ev: f.ev, life: 99 };
      } else if (f.kind === "end") {
        this.S = Object.assign({}, this.S, { done: true, success: f.ev.success, steps: f.ev.steps });
        this.overlay = { kind: "end", ev: f.ev, life: 2.2 };
        this.anim = { f, phase: "hold", t: 0, dur: 2.0 };
        this.emit();
      } else if (f.kind === "note") {
        this.toast = { f, life: 3.4 };
        this.anim = { f, phase: "hold", t: 0, dur: 3.0 };
      }
    }
    applyInstant(f) {   // used when skipping ahead: the shown state still comes from the backend
      if (f.kind === "start") { this.begin(f); this.anim = null; this.overlay = null; }
      else if (f.kind === "step") { this.apply(f.ev, true); this.p = Object.assign(this.p, this.target(f.ev.action)); }
      else if (f.kind === "end") { this.S = Object.assign({}, this.S, { done: true, success: f.ev.success }); }
    }
    apply(e, quiet) {   // THE sync point: replace what is shown with the backend's state
      const before = (this.S && this.S.inventory) || {};
      if (!this.S || this.S.seed !== e.seed) { this.L = layout(e.seed); }
      this.S = snap(e, {});
      this.lastStepFrame = e;
      const out = e.outcome || "";
      if (/zombie attacked/i.test(out)) this.wiped = true;
      if (!quiet) {
        const px = this.p.x * TILE + 16, py = this.p.y * TILE - 30;
        if (/failed:/.test(out)) {
          const why = out.split("failed:")[1].replace(/\.$/, "").trim();
          this.float(px, py, (/slipped/.test(why) ? "slipped!" : "✗ " + why), /slipped/.test(why) ? "#e8e8e8" : "#ff6b5e");
        }
        ITEMS.forEach(k => { const d = (e.inventory[k] || 0) - (before[k] || 0);
          if (d > 0) this.float(px, py - 18, "+" + d + " " + k, "#fff4a8");
          if (d > 0 && (k === "wood" || k === "stone" || k === "iron")) this.burst(this.anim.to, k); });
        if (/zombie attacked/i.test(out)) {
          this.float(px, py - 36, "Zombie! lost all wood + the table", "#ff6b5e");
          this.burst({ x: this.L.table.x + 0.5, y: this.L.table.y + 0.5 }, "table"); this.shake = 0.5;
        }
        if (e.action === "shelter" && e.sheltered) this.float(px, py - 18, "shelter built", "#c7f5c4");
      }
      this.emit();
    }
    emit() { try { this.onShow(this.S, this.lastStepFrame); } catch (_) {} }
    float(x, y, text, color) { this.floats.push({ x, y, text, color, life: 1.6 }); }
    burst(at, kind) {
      const col = { wood: C.plank, stone: C.rock2, iron: C.ore, table: C.plank }[kind] || "#fff";
      for (let i = 0; i < 14; i++) this.parts.push({ x: at.x * TILE + 16, y: at.y * TILE + 8, vx: (Math.random() - .5) * 140,
        vy: -Math.random() * 160, life: .7 + Math.random() * .4, c: col });
    }
    target(action) {
      const L = this.L, n = k => (this.tries[k] = (this.tries[k] || 0) + 1) - 1;
      if (action === "collect wood") { const t = L.trees[n("wood") % L.trees.length]; return { x: t.x + 0.1, y: t.y + 0.75 }; }
      if (action === "collect stone") { const t = L.rocks[n("stone") % L.rocks.length]; return { x: t.x + 0.1, y: t.y + 0.7 }; }
      if (action === "collect iron") { const t = L.ores[n("iron") % L.ores.length]; return { x: t.x + 0.1, y: t.y + 0.7 }; }
      if (action && action.startsWith("craft")) return { x: L.table.x + 1.1, y: L.table.y + 0.4 };
      if (action === "shelter") return { x: L.hut.x + 0.6, y: L.hut.y + 2.2 };
      return { x: this.p.x, y: this.p.y };   // invalid action: stands still, looking confused
    }

    // ---- time -----------------------------------------------------------------------------
    update(dt) {
      this.t += dt;
      const sp = this.speed;
      this.parts = this.parts.filter(p => (p.life -= dt) > 0); this.parts.forEach(p => { p.x += p.vx * dt; p.y += p.vy * dt; p.vy += 420 * dt; });
      this.floats = this.floats.filter(f => (f.life -= dt) > 0); this.floats.forEach(f => f.y -= 22 * dt);
      if (this.overlay && (this.overlay.life -= dt * sp) <= 0) this.overlay = null;
      if (this.toast && (this.toast.life -= dt * sp) <= 0) this.toast = null;
      if (this.shake) this.shake = Math.max(0, this.shake - dt);
      if (!this.anim) {
        if (this.paused && !this.stepping) return;
        const f = this.queue.shift(); if (!f) return;
        this.begin(f); if (this.stepping && f.kind !== "step") this.stepping = true;
        return;
      }
      if (this.paused && !this.stepping) return;
      const a = this.anim; a.t += dt * sp;
      if (a.phase === "walk") {
        const k = Math.min(1, a.t / a.dur); this.p.x = a.from.x + (a.to.x - a.from.x) * k; this.p.y = a.from.y + (a.to.y - a.from.y) * k;
        this.walk += dt * sp * 10;
        if (k >= 1) { a.phase = "act"; a.t = 0; a.dur = 0.45; }
      } else if (a.phase === "act") {
        this.swing = Math.sin(a.t / a.dur * Math.PI * 2);
        if (a.t >= a.dur) { this.swing = 0; this.apply(a.f.ev); a.phase = "hold"; a.t = 0; a.dur = 0.55; }
      } else if (a.t >= a.dur) {
        const wasStep = a.f.kind === "step";
        this.anim = null;
        if (this.stepping && wasStep) { this.stepping = false; }
      }
    }

    // ---- drawing --------------------------------------------------------------------------
    draw() {
      const g = this.g, L = this.L;
      g.save();
      if (this.shake) g.translate((Math.random() - .5) * 8 * this.shake, (Math.random() - .5) * 8 * this.shake);
      for (let y = 0; y < ROWS; y++) for (let x = 0; x < COLS; x++) {
        const s = L.shade[y][x], inCave = x >= 22 && y <= 8, nearBase = Math.hypot(x - 15, y - 8) < 3.2;
        g.fillStyle = inCave ? (s < .5 ? C.cave : C.cave2) : nearBase ? (s < .5 ? C.dirt : C.dirt2) : (s < .33 ? C.grass1 : s < .66 ? C.grass2 : C.grass3);
        g.fillRect(x * TILE, y * TILE, TILE, TILE);
        if (!inCave && !nearBase && s > .93) { g.fillStyle = "#e9e36a"; g.fillRect(x * TILE + 10, y * TILE + 12, 3, 3); g.fillStyle = "#f6f6f6"; g.fillRect(x * TILE + 20, y * TILE + 20, 3, 3); }
      }
      // cave mouth label + pond
      g.fillStyle = "rgba(0,0,0,.25)"; g.fillRect(22 * TILE, 9 * TILE - 4, 8 * TILE, 4);
      const pd = L.pond; g.fillStyle = C.water; roundRect(g, pd.x * TILE, pd.y * TILE, pd.w * TILE, pd.h * TILE, 18); g.fill();
      g.fillStyle = C.water2; g.fillRect(pd.x * TILE + 18, pd.y * TILE + 22 + Math.sin(this.t * 2) * 2, 30, 3);
      label(g, "FOREST · wood", 1.2 * TILE, 54); label(g, "QUARRY · stone", 9.3 * TILE, 11.7 * TILE); label(g, "MINE · iron (needs a pickaxe)", 22.3 * TILE, 54);
      // objects, back to front
      const S = this.S || { inventory: {} }, inv = S.inventory || {};
      const objs = [];
      L.trees.forEach(t => objs.push({ y: t.y + 1, d: () => drawTree(g, t.x * TILE, t.y * TILE) }));
      L.rocks.forEach(t => objs.push({ y: t.y + 0.8, d: () => drawRock(g, t.x * TILE, t.y * TILE, false) }));
      L.ores.forEach(t => objs.push({ y: t.y + 0.8, d: () => drawRock(g, t.x * TILE, t.y * TILE, true) }));
      if (inv.table) objs.push({ y: L.table.y + 1, d: () => drawTable(g, L.table.x * TILE, L.table.y * TILE) });
      else { g.strokeStyle = "rgba(255,255,255,.35)"; g.setLineDash([4, 4]); g.strokeRect(L.table.x * TILE + 2, L.table.y * TILE + 6, 28, 22); g.setLineDash([]); }
      if (S.sheltered) objs.push({ y: L.hut.y + 2, d: () => drawHut(g, L.hut.x * TILE, L.hut.y * TILE) });
      const z = this.zombiePos();
      if (z) objs.push({ y: z.y, d: () => drawZombie(g, z.x * TILE, z.y * TILE, this.t, z.attack) });
      objs.push({ y: this.p.y, d: () => drawPlayer(g, this.p.x * TILE, this.p.y * TILE, this.p.face, this.anim && this.anim.phase === "walk" ? this.walk : 0, this.swing, inv) });
      objs.sort((a, b) => a.y - b.y).forEach(o => o.d());
      this.parts.forEach(p => { g.fillStyle = p.c; g.fillRect(p.x, p.y, 4, 4); });
      g.restore();
      this.floats.forEach(f => { g.globalAlpha = Math.min(1, f.life); text(g, f.text, f.x, f.y, 15, f.color, true); g.globalAlpha = 1; });
      if (this.bubble && this.S && this.anim && this.anim.f.kind === "step") this.drawBubble();
      this.drawHud();
      if (this.overlay) this.drawOverlay();
      if (this.toast) this.drawToast();
      if (this.paused) { text(g, this.stepping ? "STEP" : "PAUSED", W - 70, H - 64, 14, "#fff", true); }
    }
    zombiePos() {
      const S = this.S; if (!S || !S.hazard || S.step < CUE_STEP && !this.wiped) return null;
      const L = this.L;
      if (S.sheltered && !this.wiped) return { x: L.hut.x + 2.6 + Math.sin(this.t * 3) * .15, y: L.hut.y + 2, attack: false };
      if (this.wiped) { const atk = this.anim && this.anim.f.kind === "step" && /zombie attacked/i.test(this.anim.f.ev.outcome || "") && this.anim.phase !== "walk";
        return atk ? { x: this.p.x + 1.1, y: this.p.y, attack: true } : { x: 24 + Math.sin(this.t) * .3, y: 13, attack: false }; }
      const k = Math.min(1, (S.step - CUE_STEP + 1) / (ZOMBIE_STEP - CUE_STEP + 1));   // closer each step after the groaning
      return { x: L.zspawn.x + (this.p.x + 1.2 - L.zspawn.x) * k * .85, y: L.zspawn.y + (this.p.y - L.zspawn.y) * k * .85, attack: false };
    }
    drawBubble() {
      const g = this.g, e = this.bubble.ev, human = e.mode === "human";
      const who_ = human ? "You" : e.mode === "mock" ? "Mock agent" : "Gemini";
      const raw = String(e.raw == null ? "" : e.raw).replace(/\s+/g, " ").trim().slice(0, 38);
      const line = human ? `${who_}: ${e.action}` : `${who_}: "${raw}"` + (raw.toLowerCase() === e.action ? "" : `  → ${e.action}`);
      g.font = "bold 14px system-ui,Segoe UI,sans-serif";
      const w = g.measureText(line).width + 22, x = clamp(this.p.x * TILE + 16 - w / 2, 6, W - w - 6), y = Math.max(46, this.p.y * TILE - 64);
      g.fillStyle = "rgba(255,255,255,.95)"; roundRect(g, x, y, w, 28, 8); g.fill();
      g.strokeStyle = human ? "#8e5bd8" : e.mode === "mock" ? "#b7791f" : "#1a7f4b"; g.lineWidth = 2; g.stroke(); g.lineWidth = 1;
      g.fillStyle = "#1c2128"; g.fillText(line, x + 11, y + 19);
    }
    drawHud() {
      const g = this.g, S = this.S;
      g.fillStyle = "rgba(18,22,28,.82)"; g.fillRect(0, 0, W, 38);
      const m = modeInfo(S, this.context);
      g.font = "bold 13px system-ui,Segoe UI,sans-serif"; const mw = g.measureText(m.text).width + 20;
      g.fillStyle = m.color; roundRect(g, 8, 7, mw, 24, 12); g.fill(); g.fillStyle = "#0e1116"; g.fillText(m.text, 18, 24);
      if (!S) { text(g, "Waiting for a game… press “Start a new game”, Start, or play it yourself.", mw + 24, 24, 14, "#e6eaf0"); return; }
      const who = S.mode === "human" ? "you are playing" : `harness ${S.version || "?"}${S.label ? " · " + S.label : ""}`;
      let title = `${(S.suite || "").toUpperCase()} · world ${S.seed} · goal: ${S.task} · ${who}`;
      g.font = "bold 14px system-ui,Segoe UI,sans-serif";
      while (title.length > 8 && g.measureText(title).width > W - 270 - (mw + 24)) title = title.slice(0, -2);
      if (title !== `${(S.suite || "").toUpperCase()} · world ${S.seed} · goal: ${S.task} · ${who}`) title += "…";
      text(g, title, mw + 24, 24, 14, "#e6eaf0");
      // step bar
      const bx = W - 250, k = Math.min(1, (S.step || 0) / (S.max_steps || 14));
      g.fillStyle = "#2a3340"; roundRect(g, bx, 12, 160, 14, 7); g.fill();
      g.fillStyle = k > .8 ? "#ef6f61" : "#7aa2ff"; roundRect(g, bx, 12, Math.max(14, 160 * k), 14, 7); g.fill();
      text(g, `step ${S.step || 0}/${S.max_steps || 14}`, bx + 168, 24, 13, "#e6eaf0");
      // hotbar (inventory straight from the backend)
      const inv = S.inventory || {}, hx = 10, hy = H - 56;
      ITEMS.forEach((k2, i) => { const x = hx + i * 50; g.fillStyle = "rgba(18,22,28,.78)"; roundRect(g, x, hy, 46, 46, 6); g.fill();
        g.strokeStyle = (inv[k2] || 0) > 0 ? "#f5d76e" : "#3b4554"; g.lineWidth = 2; g.stroke(); g.lineWidth = 1;
        drawIcon(g, k2, x + 23, hy + 21, (inv[k2] || 0) > 0);
        text(g, String(inv[k2] || 0), x + 34, hy + 42, 12, (inv[k2] || 0) > 0 ? "#fff" : "#6b7685", true); });
      text(g, "inventory (from the game engine)", hx, hy - 6, 11, "rgba(255,255,255,.85)");
      // threat panel (MiniCraft has no health points: the zombie destroys wood and the table instead)
      let th = { t: "No threat", c: "#c7f5c4" };
      if (this.wiped) th = { t: "Zombie attack: wood + table lost", c: "#ff8b80" };
      else if (S.sheltered) th = { t: "Sheltered: safe from zombies", c: "#c7f5c4" };
      else if (S.cue) th = { t: "Groaning nearby: zombie coming!", c: "#ffb86b" };
      g.font = "bold 13px system-ui,Segoe UI,sans-serif"; const tw = g.measureText(th.t).width + 20;
      g.fillStyle = "rgba(18,22,28,.82)"; roundRect(g, W - tw - 10, H - 40, tw, 30, 8); g.fill(); text(g, th.t, W - tw, H - 20, 13, th.c);
      if (this.skipped) text(g, `skipped ${this.skipped} steps` + (this.context === "replay" ? "" : " to stay live"), W - 250, H - 52, 12, "rgba(255,255,255,.85)");
    }
    drawOverlay() {
      const g = this.g, o = this.overlay, S = this.S || {};
      g.globalAlpha = Math.min(1, o.life * 1.5);
      g.fillStyle = "rgba(10,12,16,.72)"; roundRect(g, W / 2 - 300, H / 2 - 70, 600, 140, 14); g.fill();
      if (o.kind === "start") {
        text(g, `${(S.suite || "").toUpperCase()} WORLD ${S.seed}`, W / 2, H / 2 - 28, 22, "#fff", true);
        text(g, `Goal: craft a ${S.task}${S.hazard ? "  ·  something lurks here…" : ""}`, W / 2, H / 2 + 4, 16, "#e6eaf0", true);
        text(g, S.mode === "human" ? "You are playing: the Python engine decides every outcome" : `Played by harness ${S.version}${S.label ? " (" + S.label + ")" : ""}`, W / 2, H / 2 + 34, 14, "#aab4c2", true);
      } else {
        const ok = o.ev.success;
        text(g, ok ? "SUCCESS" : "FAILED", W / 2, H / 2 - 22, 30, ok ? "#6fe3a1" : "#ff7b6e", true);
        text(g, ok ? `crafted the ${S.task} in ${o.ev.steps} steps` : `no ${S.task} after ${o.ev.steps} steps`, W / 2, H / 2 + 10, 16, "#e6eaf0", true);
        const ms = (o.ev.mistakes || []).filter(x => x !== "slip");
        text(g, ms.length ? "mistakes: " + ms.join(", ") : "no avoidable mistakes", W / 2, H / 2 + 38, 14, "#aab4c2", true);
      }
      g.globalAlpha = 1;
    }
    drawToast() {
      const g = this.g, f = this.toast.f, col = { ok: "#6fe3a1", bad: "#ff7b6e", warn: "#ffcf66", idea: "#9fc2ff" }[f.tone];
      g.globalAlpha = Math.min(1, this.toast.life * 1.5);
      g.fillStyle = "rgba(10,12,16,.86)"; roundRect(g, 60, 56, W - 120, 74, 12); g.fill();
      g.strokeStyle = col; g.lineWidth = 2; g.stroke(); g.lineWidth = 1;
      text(g, f.text, W / 2, 86, 17, col, true);
      text(g, (f.sub || "").slice(0, 110), W / 2, 112, 14, "#e6eaf0", true);
      g.globalAlpha = 1;
    }
  }

  // ---- helpers ------------------------------------------------------------------------------
  function snap(e, over) {
    return Object.assign({ mode: e.mode, suite: e.suite, label: e.label, version: e.version, seed: e.seed, task: e.task,
      hazard: !!e.hazard, max_steps: e.max_steps, step: e.step, inventory: Object.assign({}, e.inventory || {}), sheltered: !!e.sheltered,
      cue: !!e.cue, outcome: e.outcome || "", raw: e.raw, action: e.action, cached: !!e.cached, latency_ms: e.latency_ms,
      call_id: e.call_id, episode: e.episode }, over);
  }
  function who(model) { return model ? (String(model).startsWith("mock") ? "The mock proposer" : model) : "The proposer"; }
  function modeInfo(S, ctx) {
    let m;
    if (!S) m = ctx === "human" ? { text: "HUMAN PLAYER · no AI", color: "#c9a7ff" } : { text: ctx === "replay" ? "REPLAY" : "IDLE", color: "#aab4c2" };
    else if (S.mode === "human") m = { text: "HUMAN PLAYER · no AI, no API calls", color: "#c9a7ff" };
    else if (S.mode === "mock") m = { text: "MOCK · simulated agent, no model called", color: "#ffcf66" };
    else if (S.mode === "real") m = { text: "REAL GEMINI · " + (S.step ? (S.cached ? "cached reply" : "LIVE call " + (S.latency_ms || 0) + " ms") : "live"), color: "#6fe3a1" };
    else m = { text: "UNKNOWN SOURCE", color: "#aab4c2" };
    if (ctx === "replay") m = { text: "REPLAY · " + m.text, color: "#9fc2ff" };
    return m;
  }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function roundRect(g, x, y, w, h, r) { g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r); g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath(); }
  function text(g, s, x, y, size, color, center) {
    g.font = `bold ${size}px system-ui,Segoe UI,sans-serif`; g.textAlign = center ? "center" : "left";
    g.lineWidth = 3; g.strokeStyle = "rgba(0,0,0,.55)"; g.strokeText(s, x, y); g.fillStyle = color; g.fillText(s, x, y);
    g.textAlign = "left"; g.lineWidth = 1;
  }
  function label(g, s, x, y) { g.font = "bold 11px system-ui,Segoe UI,sans-serif"; g.fillStyle = "rgba(255,255,255,.8)"; g.fillText(s, x, y); }
  function drawTree(g, x, y) {
    g.fillStyle = "rgba(0,0,0,.18)"; g.beginPath(); g.ellipse(x + 16, y + 30, 14, 5, 0, 0, 7); g.fill();
    g.fillStyle = C.trunk; g.fillRect(x + 12, y + 12, 8, 19);
    g.fillStyle = C.leaf; g.beginPath(); g.arc(x + 16, y + 6, 15, 0, 7); g.fill();
    g.fillStyle = C.leaf2; g.beginPath(); g.arc(x + 11, y + 2, 8, 0, 7); g.arc(x + 21, y + 4, 7, 0, 7); g.fill();
  }
  function drawRock(g, x, y, ore) {
    g.fillStyle = "rgba(0,0,0,.2)"; g.beginPath(); g.ellipse(x + 16, y + 27, 14, 5, 0, 0, 7); g.fill();
    g.fillStyle = ore ? "#7d7a86" : C.rock; g.beginPath(); g.moveTo(x + 3, y + 26); g.lineTo(x + 7, y + 10); g.lineTo(x + 17, y + 5); g.lineTo(x + 27, y + 11); g.lineTo(x + 30, y + 26); g.closePath(); g.fill();
    g.fillStyle = ore ? "#97949f" : C.rock2; g.beginPath(); g.moveTo(x + 9, y + 12); g.lineTo(x + 17, y + 7); g.lineTo(x + 22, y + 12); g.lineTo(x + 14, y + 15); g.closePath(); g.fill();
    if (ore) { g.fillStyle = C.ore; [[10, 18], [19, 14], [23, 21], [14, 22]].forEach(([a, b]) => g.fillRect(x + a, y + b, 4, 4)); }
  }
  function drawTable(g, x, y) {
    g.fillStyle = "rgba(0,0,0,.2)"; g.fillRect(x + 3, y + 26, 28, 4);
    g.fillStyle = C.wood; g.fillRect(x + 4, y + 14, 4, 14); g.fillRect(x + 24, y + 14, 4, 14);
    g.fillStyle = C.plank; g.fillRect(x + 1, y + 8, 30, 8); g.fillStyle = "#8e5a2b"; g.fillRect(x + 1, y + 15, 30, 2);
    g.fillStyle = "#6b6f78"; g.fillRect(x + 6, y + 4, 6, 4); g.fillStyle = "#d9d2c0"; g.fillRect(x + 18, y + 5, 8, 3);
  }
  function drawHut(g, x, y) {
    g.fillStyle = "rgba(0,0,0,.2)"; g.fillRect(x + 2, y + 60, 62, 6);
    g.fillStyle = "#9b6b3d"; g.fillRect(x + 6, y + 26, 54, 36);
    g.fillStyle = "#7a4f2a"; for (let i = 0; i < 4; i++) g.fillRect(x + 6, y + 32 + i * 8, 54, 2);
    g.fillStyle = "#b3412f"; g.beginPath(); g.moveTo(x, y + 28); g.lineTo(x + 33, y + 2); g.lineTo(x + 66, y + 28); g.closePath(); g.fill();
    g.fillStyle = "#3a2414"; g.fillRect(x + 26, y + 42, 14, 20);
  }
  function drawPlayer(g, x, y, face, walk, swing, inv) {
    const bob = walk ? Math.abs(Math.sin(walk)) * 2 : 0, cx = x + 16, by = y + 22 - bob;
    g.fillStyle = "rgba(0,0,0,.25)"; g.beginPath(); g.ellipse(cx, y + 31, 10, 4, 0, 0, 7); g.fill();
    const l = walk ? Math.sin(walk) * 3 : 0;
    g.fillStyle = C.pants; g.fillRect(cx - 6, by + 2, 5, 8 + l); g.fillRect(cx + 1, by + 2, 5, 8 - l);
    g.fillStyle = C.shirt; g.fillRect(cx - 8, by - 12, 16, 15);
    g.fillStyle = C.skin; g.beginPath(); g.arc(cx, by - 18, 8, 0, 7); g.fill();
    g.fillStyle = C.hair; g.fillRect(cx - 8, by - 27, 16, 5);
    g.fillStyle = "#222"; g.fillRect(cx + face * 3 - 1, by - 19, 2, 3);
    // arm + held tool
    const ang = -0.6 + swing * 1.1, ax = cx + face * 8, ay = by - 8;
    g.save(); g.translate(ax, ay); g.rotate(face * ang);
    g.fillStyle = C.skin; g.fillRect(-2, 0, 4, 10);
    if (inv.sword) { g.fillStyle = "#cfd8e3"; g.fillRect(-2, 8, 3, 16); g.fillStyle = C.trunk; g.fillRect(-4, 8, 8, 3); }
    else if (inv.pickaxe) { g.fillStyle = C.trunk; g.fillRect(-1, 6, 3, 16); g.fillStyle = "#9aa0a6"; g.fillRect(-7, 20, 15, 4); }
    g.restore();
  }
  function drawZombie(g, x, y, t, attack) {
    const cx = x + 16, by = y + 22 + Math.sin(t * 4) * 1;
    g.fillStyle = "rgba(0,0,0,.25)"; g.beginPath(); g.ellipse(cx, y + 31, 10, 4, 0, 0, 7); g.fill();
    g.fillStyle = "#3d4a33"; g.fillRect(cx - 6, by + 2, 5, 9); g.fillRect(cx + 1, by + 2, 5, 9);
    g.fillStyle = C.zshirt; g.fillRect(cx - 8, by - 12, 16, 15);
    g.fillStyle = C.zskin; g.beginPath(); g.arc(cx, by - 18, 8, 0, 7); g.fill();
    g.fillStyle = "#c0392b"; g.fillRect(cx - 5, by - 20, 3, 3); g.fillRect(cx + 2, by - 20, 3, 3);
    g.fillStyle = C.zskin; const reach = attack ? 6 + Math.abs(Math.sin(t * 12)) * 6 : 6;
    g.fillRect(cx - 10 - reach, by - 9, 10 + reach, 4); g.fillRect(cx - 10 - reach, by - 3, 10 + reach, 4);
  }
  function drawIcon(g, k, x, y, on) {
    g.globalAlpha = on ? 1 : .35;
    if (k === "wood") { g.fillStyle = C.wood; g.fillRect(x - 12, y - 6, 24, 12); g.fillStyle = C.plank; g.beginPath(); g.arc(x + 12, y, 6, 0, 7); g.fill(); }
    else if (k === "stone") { g.fillStyle = C.rock; g.beginPath(); g.arc(x, y, 10, 0, 7); g.fill(); g.fillStyle = C.rock2; g.beginPath(); g.arc(x - 3, y - 3, 4, 0, 7); g.fill(); }
    else if (k === "iron") { g.fillStyle = "#c9ced6"; g.beginPath(); g.moveTo(x - 12, y + 6); g.lineTo(x - 7, y - 6); g.lineTo(x + 12, y - 6); g.lineTo(x + 7, y + 6); g.closePath(); g.fill(); }
    else if (k === "table") drawTable(g, x - 16, y - 16);
    else if (k === "pickaxe") { g.save(); g.translate(x, y); g.rotate(-.7); g.fillStyle = C.trunk; g.fillRect(-2, -12, 4, 24); g.fillStyle = "#9aa0a6"; g.fillRect(-10, -14, 20, 5); g.restore(); }
    else if (k === "sword") { g.save(); g.translate(x, y); g.rotate(.7); g.fillStyle = "#cfd8e3"; g.fillRect(-2, -14, 4, 20); g.fillStyle = C.trunk; g.fillRect(-6, 5, 12, 3); g.fillRect(-1, 7, 3, 6); g.restore(); }
    g.globalAlpha = 1;
  }

  global.GameView = GameView;
  global.MiniCraftLayout = layout;
})(window);
