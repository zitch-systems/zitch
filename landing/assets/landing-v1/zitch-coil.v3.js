/*!
 * Zitch coil loader — the brand "Z" as a ribbon wrapped onto a cylinder.
 *
 * One beat (2.8s by default): wind up → one full turn → settle and rest on the
 * Z pose, while the coil squashes and stretches like a spring.
 *
 * This file is the single source of truth for the animation:
 *   - the website / web prototype draw it live on a <canvas> (ZitchLoader.mount)
 *   - the mobile app plays a sprite sheet baked from the very same drawFrame()
 *     (scripts/bake-loader-sprite.cjs → assets/images/zitch-loader-sprite.png)
 * Change the look here, re-run the bake script, and every surface stays identical.
 *
 * No dependencies, no network, no innerHTML. Versioned filename because
 * /assets/* is served with an immutable one-year cache.
 *
 * v3: a coil can play a set number of beats and then rest on the Z pose with
 * no animation frame running (opts.loops), and replay() plays it again. A logo
 * that spins forever redraws its canvas sixty times a second for as long as
 * the page is open, which on a phone is most of the scrolling budget.
 */
(function (root) {
  'use strict';

  // ---------- geometry ----------
  var R = 60;                          // coil radius
  var SWEEP = 520 * Math.PI / 180;     // front bar → back fold → front bar, like the Z
  var A0 = -80 * Math.PI / 180;        // start at the front-left, like the logo's top bar
  var PITCH = 88;                      // vertical rise per turn
  var HALF = 14.4;                     // half the ribbon width
  var STEP = 1.5 * Math.PI / 180;      // segment size — keeps the curve perfectly smooth
  var TILT = -10 * Math.PI / 180;      // camera looks slightly down on the coil
  var PERSP = 820;                     // perspective distance
  var AMID = A0 + SWEEP / 2;
  var NSEG = Math.ceil(SWEEP / STEP);
  var cosT = Math.cos(TILT), sinT = Math.sin(TILT);

  // Logical drawing box (units = CSS px at scale 1). The coil is centred on (0,0).
  var BOX_W = 140;                     // coil only: 140 × 214
  var BOX_TOP = 107;                   // distance from the top of the box to the coil centre
  var BOX_H = 214;
  var BOX_H_SHADOW = 232;              // extra room below for the contact shadow

  // ---------- light ----------
  var L = norm([-0.45, -0.62, 0.64]);                          // key light: upper-left, in front
  var OUT_LIT = [43, 234, 246], OUT_SHADE = [8, 146, 164];     // glossy outside of the ribbon (#2BEAF6)
  var IN_LIT = [14, 138, 154], IN_SHADE = [4, 56, 66];         // shaded inside, seen when it passes behind
  var WHITE = [255, 255, 255], DEEP = [0, 70, 82];

  function norm(v) { var l = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) || 1; return [v[0] / l, v[1] / l, v[2] / l]; }
  function mix(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }
  function css(c) { return 'rgb(' + (c[0] | 0) + ',' + (c[1] | 0) + ',' + (c[2] | 0) + ')'; }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }

  // cubic-bezier(x1,y1,x2,y2) easing, solved with Newton's method
  function bezier(x1, y1, x2, y2) {
    function f(a, b, t) { return ((1 - 3 * b + 3 * a) * t + (3 * b - 6 * a)) * t * t + 3 * a * t; }
    function df(a, b, t) { return 3 * (1 - 3 * b + 3 * a) * t * t + 2 * (3 * b - 6 * a) * t + 3 * a; }
    return function (x) {
      if (x <= 0) return 0; if (x >= 1) return 1;
      var t = x;
      for (var i = 0; i < 8; i++) { var d = df(x1, x2, t); if (Math.abs(d) < 1e-6) break; t -= (f(x1, x2, t) - x) / d; }
      return f(y1, y2, clamp(t, 0, 1));
    };
  }
  var spinEase = bezier(.68, 0, .2, 1);
  // one full turn with a wind-up and a settle, then a short rest on the Z pose
  function spinAt(p) { return p < .78 ? spinEase(p / .78) : 1; }
  // spring: squash in anticipation → stretch at full speed → overshoot → rest
  var SPRING = [[0, 1], [.14, .86], [.46, 1.1], [.76, .95], [.88, 1.02], [1, 1]];
  function springAt(p) {
    for (var i = 1; i < SPRING.length; i++) {
      if (p <= SPRING[i][0]) {
        var a = SPRING[i - 1], b = SPRING[i], u = (p - a[0]) / (b[0] - a[0]);
        u = (1 - Math.cos(Math.PI * u)) / 2;
        return a[1] + (b[1] - a[1]) * u;
      }
    }
    return 1;
  }

  // project a point on the cylinder: angle a (already spun), height y
  function project(a, y) {
    var x = R * Math.sin(a), z = R * Math.cos(a);
    var y2 = y * cosT - z * sinT, z2 = y * sinT + z * cosT;
    var k = PERSP / (PERSP - z2);
    return [x * k, y2 * k];
  }

  /**
   * Draw one frame. The context must already be transformed so that (0,0) is the
   * coil centre and one unit is one logical px.
   *   p       0..1 position within the beat
   *   reveal  0..1 how much of the ribbon is drawn (the write-on entrance); default 1
   *   shadow  draw the contact shadow under the coil; default false
   */
  function drawFrame(ctx, p, opts) {
    opts = opts || {};
    var reveal = opts.reveal == null ? 1 : opts.reveal;
    var spin = -2 * Math.PI * spinAt(p);
    var spring = springAt(p);
    var limit = A0 + SWEEP * reveal;
    var half = HALF * spring;
    var STEPc = STEP * (opts.coarse > 1 ? opts.coarse : 1), NSEGc = Math.ceil(SWEEP / STEPc);   // small coils use longer segments: same look, far fewer fills

    if (opts.shadow) {
      // contact shadow — tightens as the coil stretches, spreads as it compresses
      var sx = 1 + (1 - spring) * 0.9;
      var sa = clamp(0.5 - (spring - 1) * 1.6, 0.2, 0.62) * Math.min(1, reveal * 1.5) * (opts.shadowStrength == null ? 1 : opts.shadowStrength);
      ctx.save();
      ctx.translate(0, 112); ctx.scale(sx, 0.13);
      var sg = ctx.createRadialGradient(0, 0, 0, 0, 0, 64);
      sg.addColorStop(0, 'rgba(0,0,0,' + sa.toFixed(3) + ')'); sg.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = sg; ctx.beginPath(); ctx.arc(0, 0, 64, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
    }

    // build the ribbon as lit quads
    var segs = [];
    for (var i = 0; i < NSEGc; i++) {
      var a1 = A0 + i * STEPc, a2 = Math.min(A0 + SWEEP, a1 + STEPc);
      if (a1 >= limit) break;
      if (a2 > limit) a2 = limit;
      var am = (a1 + a2) / 2;
      // slight overlap with the neighbours so no hairline seams show between quads
      var b1 = Math.max(A0, a1 - STEP * .35), b2 = Math.min(limit, a2 + STEP * .35);
      var y1 = (b1 - AMID) / (2 * Math.PI) * PITCH * spring;
      var y2 = (b2 - AMID) / (2 * Math.PI) * PITCH * spring;
      var ym = (am - AMID) / (2 * Math.PI) * PITCH * spring;
      var q = {
        tl: project(b1 + spin, y1 - half), tr: project(b2 + spin, y2 - half),
        br: project(b2 + spin, y2 + half), bl: project(b1 + spin, y1 + half)
      };
      var as = am + spin;
      // surface normal (pointing out of the cylinder) and centre, after the camera tilt
      var n = [Math.sin(as), -Math.cos(as) * sinT, Math.cos(as) * cosT];
      var cz = ym * sinT + R * Math.cos(as) * cosT;
      var v = norm([-R * Math.sin(as), -(ym * cosT - R * Math.cos(as) * sinT), PERSP - cz]);
      var facing = n[0] * v[0] + n[1] * v[1] + n[2] * v[2];
      var col;
      if (facing >= 0) {                                         // glossy outside
        var diff = Math.max(0, n[0] * L[0] + n[1] * L[1] + n[2] * L[2]);
        var hv = norm([L[0] + v[0], L[1] + v[1], L[2] + v[2]]);
        var spec = Math.pow(Math.max(0, n[0] * hv[0] + n[1] * hv[1] + n[2] * hv[2]), 38);
        col = mix(OUT_SHADE, OUT_LIT, clamp(.42 + .62 * diff, 0, 1));
        col = mix(col, WHITE, spec * .55);
        q.top = mix(col, WHITE, .40); q.mid1 = mix(col, WHITE, .10); q.bot = mix(col, DEEP, .30);
        q.outer = true;
      } else {                                                   // shaded inside
        var di = Math.max(0, -(n[0] * L[0] + n[1] * L[1] + n[2] * L[2]));
        var fog = .72 + .28 * clamp((cz + R) / (2 * R), 0, 1);   // further away = dimmer
        col = mix(IN_SHADE, IN_LIT, clamp(.25 + .65 * di, 0, 1));
        col = [col[0] * fog, col[1] * fog, col[2] * fog];
        q.top = mix(col, [0, 22, 28], .30); q.bot = mix(col, WHITE, .06);
        q.outer = false;
      }
      q.col = col; q.z = cz;
      segs.push(q);
    }
    segs.sort(function (a, b) { return a.z - b.z; });            // far → near

    for (var j = 0; j < segs.length; j++) {
      var s = segs[j];
      var g = ctx.createLinearGradient(
        (s.tl[0] + s.tr[0]) / 2, (s.tl[1] + s.tr[1]) / 2,
        (s.bl[0] + s.br[0]) / 2, (s.bl[1] + s.br[1]) / 2);
      g.addColorStop(0, css(s.top));
      if (s.outer) { g.addColorStop(.26, css(s.mid1)); g.addColorStop(.52, css(s.col)); }
      else { g.addColorStop(.45, css(s.col)); }
      g.addColorStop(1, css(s.bot));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.moveTo(s.tl[0], s.tl[1]); ctx.lineTo(s.tr[0], s.tr[1]);
      ctx.lineTo(s.br[0], s.br[1]); ctx.lineTo(s.bl[0], s.bl[1]);
      ctx.closePath(); ctx.fill();
    }
    return spring;
  }

  /**
   * Mount a live loader inside `target` (an element). Returns { destroy, setDuration }.
   *   height    rendered height in CSS px (width follows the 140:214 box)   default 120
   *   duration  seconds per beat                                            default 2.8
   *   shadow    contact shadow under the coil                               default false
   *   drawOn    ribbon writes itself on when mounted                        default true
   *   label     accessible name                                             default 'Loading'
 *   decorative  hide from assistive tech (logo / background use)            default false
 *   maxDensity  cap the backing-store density for very large canvases      default none
 *   fps         cap the frame rate (large background coils)                default none
 *   loops       beats to play before resting on the Z pose; 0 = forever     default 0
   * The loop stops by itself if the canvas leaves the document, so a forgotten
   * destroy() never leaks a requestAnimationFrame loop.
   */
  function mount(target, opts) {
    opts = opts || {};
    var height = opts.height || 120;
    var dur = opts.duration || 2.8;
    var shadow = !!opts.shadow;
    var boxH = shadow ? BOX_H_SHADOW : BOX_H;
    var unit = height / boxH;                                    // CSS px per logical unit
    var cssW = Math.round(BOX_W * unit), cssH = Math.round(height);
    var reduce = !!(root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches);

    var canvas = document.createElement('canvas');
    if (opts.decorative) canvas.setAttribute('aria-hidden', 'true');
    else { canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', opts.label || 'Loading'); }
    canvas.style.cssText = 'display:block;width:' + cssW + 'px;height:' + cssH + 'px;';
    target.appendChild(canvas);
    var ctx = canvas.getContext('2d');

    var density = 0, phase = 0, last = 0, t0 = 0, raf = 0, dead = false, played = 0;
    var loops = opts.loops > 0 ? opts.loops : 0;
    var gap = opts.fps ? 1000 / opts.fps - 3 : 0;

    function resize() {
      var dpr = root.devicePixelRatio || 1;
      // supersample so it stays sharp on retina / when zoomed; large decorative coils cap the density instead
      var d = opts.maxDensity ? Math.min(opts.maxDensity, Math.max(1, dpr)) : Math.min(4, Math.max(2, dpr) * 1.5);
      if (d === density) return;
      density = d;
      canvas.width = Math.round(cssW * d);
      canvas.height = Math.round(cssH * d);
    }
    function paint(p, reveal) {
      var k = unit * density;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(k, 0, 0, k, canvas.width / 2, BOX_TOP * k);
      // aim for segments about 4.5 device px long
      var coarse = Math.max(1, Math.min(4, Math.round(4.5 / (R * STEP * k))));
      drawFrame(ctx, p, { reveal: reveal, shadow: shadow, shadowStrength: opts.shadowStrength, coarse: coarse });
    }
    function frame(now) {
      if (dead) return;
      if (!canvas.isConnected) { dead = true; return; }
      if (!t0) { t0 = now; last = now; }
      if (!last) last = now;
      if (gap && now - last < gap) { raf = root.requestAnimationFrame(frame); return; }   // decorative coils can run at a lower frame rate
      var dt = Math.min(100, now - last); last = now;
      played += dt / (dur * 1000);
      phase = (phase + dt / (dur * 1000)) % 1;                   // phase carries over when the speed changes
      var u = opts.drawOn === false ? 1 : clamp((now - t0 - 80) / 800, 0, 1);
      resize();
      if (loops && played >= loops && u >= 1) { paint(0, 1); raf = 0; return; }   // done: rest on the Z, no frame loop left running
      paint(phase, 1 - Math.pow(1 - u, 3));
      raf = root.requestAnimationFrame(frame);
    }
    function replay(n) {
      if (dead || reduce) return;
      if (n > 0) loops = n;
      played = 0; phase = 0;
      if (!raf) { last = 0; t0 = 0; opts.drawOn = false; raf = root.requestAnimationFrame(frame); }
    }
    function onResize() { if (dead) return; var d = density; resize(); if (reduce && d !== density) paint(0, 1); }

    resize();
    if (reduce) paint(0, 1);                                     // still Z pose for reduced-motion users
    else raf = root.requestAnimationFrame(frame);
    root.addEventListener('resize', onResize);

    return {
      canvas: canvas,
      setDuration: function (s) { if (s > 0) dur = s; },
      replay: replay,
      destroy: function () {
        dead = true;
        if (raf) root.cancelAnimationFrame(raf);
        root.removeEventListener('resize', onResize);
        if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
      }
    };
  }

  root.ZitchLoader = {
    mount: mount,
    drawFrame: drawFrame,
    BOX: { w: BOX_W, h: BOX_H, top: BOX_TOP, hShadow: BOX_H_SHADOW },
    DURATION: 2.8
  };
})(typeof window !== 'undefined' ? window : this);
