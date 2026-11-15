/*
 * Zitch active liveness for Tier 2: turn left, turn right, smile.
 *
 * Runs entirely on the customer's device with MediaPipe FaceLandmarker
 * (vendored next to this file, served by our own static host, so the page's
 * `script-src 'nonce-…'` and `connect-src 'self'` still hold). Nothing here
 * talks to a server: it hands the best frontal frame back to the page, which
 * sends it to the bank exactly as before. The bank's face match stays the
 * decision; this is anti-spoofing and a better photo.
 *
 * Shared by the WhatsApp face page and the app's in-app check.
 *
 *   ZitchLiveness.load(assets)            -> Promise<landmarker>
 *   ZitchLiveness.session(lm, video, ui)  -> { stop() }
 *   ZitchLiveness.stage(document)         -> ui callbacks for the shared markup
 *   ZitchLiveness.wakeLock()              -> { release() }
 *   ZitchLiveness.snapshot(source, w, h, mirror) -> JPEG data URL
 *
 * Classic script, no modules: the MediaPipe CommonJS bundle and its WASM
 * loader are added as nonced <script> elements, because MediaPipe's own
 * loader would insert an un-nonced script that a nonce-only CSP refuses.
 */
(function () {
  'use strict';

  var STEPS = ['left', 'right', 'smile'];
  // Yaw is the nose tip's offset from the middle of the face (between the
  // contour points by the ears), as a share of half the face width. Roughly
  // 1.3 x tan(angle): 0 frontal, 0.4 at ~17 degrees, 0.75 at 30 degrees.
  var TURN = 0.4;
  var FRONTAL = 0.15;
  var SMILE = 0.5;
  var EYES_OPEN = 0.45;
  var HOLD_FRAMES = 3;           // a move must hold for this many frames
  var FRAME_MS = 60;             // ~16 detections a second at most
  var SNAP_MS = 250;             // at most 4 JPEG encodes a second
  var DARK = 70;                 // mean face luma (0-255) that needs more light
  var LIGHT = 85;                // ... and the level that clears the hint
  var LOST_RESET_MS = 4000;      // face gone this long: start the moves again
  var SLOW_MS = 60000;           // offer the manual photo after this long
  var MAX_SIDE = 720;

  function now() { return (window.performance && performance.now) ? performance.now() : Date.now(); }

  function addScript(src, nonce) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = src;
      s.nonce = nonce;
      s.async = false;
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error('Could not load ' + src)); };
      document.head.appendChild(s);
    });
  }

  function create(vision, assets, delegate) {
    return vision.FaceLandmarker.createFromOptions(
      // An empty loader path tells MediaPipe the ModuleFactory is already
      // present (we loaded it with the page's nonce).
      { wasmLoaderPath: '', wasmBinaryPath: assets.wasm },
      {
        baseOptions: { modelAssetPath: assets.model, delegate: delegate },
        runningMode: 'VIDEO',
        numFaces: 2,
        outputFaceBlendshapes: true,
        minFaceDetectionConfidence: 0.5,
        minFacePresenceConfidence: 0.5,
        minTrackingConfidence: 0.5
      }
    ).then(function (lm) {
      // Warm up and prove the delegate actually runs on this device; some GPU
      // drivers create fine and then fail on the first frame.
      var c = document.createElement('canvas');
      c.width = c.height = 64;
      c.getContext('2d').fillRect(0, 0, 64, 64);
      try {
        lm.detectForVideo(c, now());
      } catch (error) {
        try { lm.close(); } catch (ignored) { /* already broken */ }
        throw error;
      }
      return lm;
    });
  }

  var loading = null;

  /** Load the self-hosted model once. Rejects on an old browser, a blocked
   * network, no WASM SIMD, or after `timeoutMs`; the page then falls back to
   * a manual photo. */
  function load(assets) {
    if (loading) return loading;
    loading = new Promise(function (resolve, reject) {
      if (typeof WebAssembly !== 'object' || !window.Promise || !window.requestAnimationFrame) {
        reject(new Error('unsupported'));
        return;
      }
      var timer = setTimeout(function () { reject(new Error('timeout')); }, assets.timeoutMs || 30000);
      // The CommonJS bundle writes its API onto a global `exports`.
      window.exports = {};
      addScript(assets.bundle, assets.nonce).then(function () {
        var vision = window.exports;
        try { delete window.exports; } catch (e) { window.exports = undefined; }
        if (!vision || !vision.FaceLandmarker || !vision.FilesetResolver) throw new Error('bundle');
        return vision.FilesetResolver.isSimdSupported().then(function (simd) {
          // Only the SIMD build is shipped (every current mobile browser has
          // it); older engines take the manual photo instead.
          if (!simd) throw new Error('simd');
          return addScript(assets.loader, assets.nonce);
        }).then(function () {
          return create(vision, assets, 'GPU').catch(function () { return create(vision, assets, 'CPU'); });
        });
      }).then(function (lm) {
        clearTimeout(timer);
        resolve(lm);
      }, function (error) {
        clearTimeout(timer);
        reject(error);
      });
    });
    return loading;
  }

  /** JPEG of a video frame or image, longest side <= 720 px. */
  function snapshot(source, width, height, mirror) {
    var scale = Math.min(1, MAX_SIDE / Math.max(width, height));
    var canvas = document.createElement('canvas');
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    var ctx = canvas.getContext('2d');
    if (mirror) { ctx.translate(canvas.width, 0); ctx.scale(-1, 1); }
    ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.85);
  }

  function blend(shapes) {
    var out = {};
    var list = shapes && shapes.categories;
    if (!list) return out;
    for (var i = 0; i < list.length; i++) out[list[i].categoryName] = list[i].score;
    return out;
  }

  /** Geometry of one face, in the frame's normalised coordinates. The frame is
   * NOT mirrored (only the preview is), so the subject's left is image right. */
  function measure(points, shapes, vw, vh) {
    var minX = 1, maxX = 0, minY = 1, maxY = 0;
    for (var i = 0; i < points.length; i++) {
      var p = points[i];
      if (p.x < minX) minX = p.x;
      if (p.x > maxX) maxX = p.x;
      if (p.y < minY) minY = p.y;
      if (p.y > maxY) maxY = p.y;
    }
    var nose = points[1];
    var a = points[234].x, b = points[454].x;
    var left = Math.min(a, b), right = Math.max(a, b);
    var half = (right - left) / 2 || 1e-6;
    // > 0: the nose moved toward image right, i.e. the customer turned to
    // THEIR left (which is screen-left in the mirrored preview).
    var yaw = (nose.x - (left + right) / 2) / half;
    var eyeY = (points[33].y + points[263].y) / 2;
    var chinY = points[152].y;
    var pitch = (nose.y - eyeY) / ((chinY - eyeY) || 1e-6);
    var s = blend(shapes);
    // The circle shows the centre square of the frame (object-fit: cover).
    var side = Math.min(vw, vh);
    var ox = (vw - side) / 2 / vw, oy = (vh - side) / 2 / vh;
    var sx = vw / side, sy = vh / side;
    return {
      yaw: yaw,
      pitch: pitch,
      smile: ((s.mouthSmileLeft || 0) + (s.mouthSmileRight || 0)) / 2,
      blink: Math.max(s.eyeBlinkLeft || 0, s.eyeBlinkRight || 0),
      // Face box in frame pixels, for the brightness sample.
      box: { x: minX * vw, y: minY * vh, w: (maxX - minX) * vw, h: (maxY - minY) * vh },
      // Centre and width relative to the visible circle (0..1).
      cx: ((minX + maxX) / 2 - ox) * sx,
      cy: ((minY + maxY) / 2 - oy) * sy,
      w: (maxX - minX) * sx
    };
  }

  var lumaCanvas = null;
  function faceLuma(video, box) {
    try {
      if (!lumaCanvas) { lumaCanvas = document.createElement('canvas'); lumaCanvas.width = lumaCanvas.height = 24; }
      var ctx = lumaCanvas.getContext('2d', { willReadFrequently: true });
      var x = Math.max(0, box.x), y = Math.max(0, box.y);
      var w = Math.min(video.videoWidth - x, box.w), h = Math.min(video.videoHeight - y, box.h);
      if (w < 4 || h < 4) return -1;
      ctx.drawImage(video, x, y, w, h, 0, 0, 24, 24);
      var d = ctx.getImageData(0, 0, 24, 24).data, sum = 0;
      for (var i = 0; i < d.length; i += 4) sum += 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
      return sum / (d.length / 4);
    } catch (e) {
      return -1;
    }
  }

  /**
   * Guide the customer through the moves on a playing <video>.
   * ui: { step(doneCount, name), prompt(name), hint(text|''), dark(bool),
   *       done(dataUrl), error(err), slow() } - every callback optional.
   */
  function session(lm, video, ui) {
    var call = function (name) {
      var fn = ui && ui[name];
      if (typeof fn === 'function') fn.apply(null, Array.prototype.slice.call(arguments, 1));
    };
    var stopped = false, raf = 0;
    var step = 0, hold = 0, frontHold = 0, errors = 0;
    var lastRun = 0, lastVideoTime = -1, lastTs = 0, lastSeen = now(), lastLuma = 0, lastSnap = 0;
    var started = now(), slowSent = false, dark = false, hint = '';
    var best = null, bestScore = -Infinity;

    function setHint(text) {
      if (text !== hint) { hint = text; call('hint', text); }
    }

    function reset() {
      step = 0; hold = 0; best = null; bestScore = -Infinity;
      call('step', 0, STEPS[0]);
      call('prompt', STEPS[0]);
    }

    function finish() {
      stopped = true;
      if (raf) cancelAnimationFrame(raf);
      call('step', STEPS.length, 'done');
      call('prompt', 'done');
      call('done', best);
    }

    function analyse(result, t) {
      var faces = (result && result.faceLandmarks) || [];
      if (!faces.length) {
        hold = 0;
        if (t - lastSeen > 1200) setHint('Keep your face inside the circle');
        if (t - lastSeen > LOST_RESET_MS && step > 0) { lastSeen = t; reset(); }
        return;
      }
      lastSeen = t;
      if (faces.length > 1) { hold = 0; setHint('Only your face should be in the circle'); return; }
      var m = measure(faces[0], result.faceBlendshapes && result.faceBlendshapes[0], video.videoWidth, video.videoHeight);
      var sized = m.w >= 0.22 && m.w <= 0.95;
      var centred = Math.abs(m.cx - 0.5) < 0.22 && Math.abs(m.cy - 0.5) < 0.25;
      if (m.w < 0.22) setHint('Move a little closer');
      else if (m.w > 0.95) setHint('Move back a little');
      else if (!centred) setHint('Centre your face in the circle');
      else setHint('');

      if (t - lastLuma > 400) {
        lastLuma = t;
        var luma = faceLuma(video, m.box);
        if (luma >= 0) {
          var nowDark = dark ? luma < LIGHT : luma < DARK;
          if (nowDark !== dark) { dark = nowDark; call('dark', dark); }
        }
      }

      // The selfie: the best frontal frame seen while the moves run.
      var frontal = Math.abs(m.yaw) < FRONTAL && m.pitch > 0.2 && m.pitch < 0.75 &&
        m.blink < EYES_OPEN && sized && centred;
      frontHold = frontal ? frontHold + 1 : 0;
      if (frontal && frontHold >= 2) {
        var score = 1 - 2.5 * Math.abs(m.yaw) - Math.abs(m.cx - 0.5) - Math.abs(m.cy - 0.5) -
          0.6 * m.blink - 0.3 * m.smile - Math.abs(m.w - 0.55) - (dark ? 0.3 : 0);
        if (score > bestScore + 0.02 && t - lastSnap > SNAP_MS) {
          lastSnap = t;
          bestScore = score;
          best = snapshot(video, video.videoWidth, video.videoHeight, true);
        }
      }

      if (step < STEPS.length) {
        var want = STEPS[step];
        var ok = sized && (want === 'left' ? m.yaw > TURN
          : want === 'right' ? m.yaw < -TURN
            : m.smile > SMILE && Math.abs(m.yaw) < 0.25);
        hold = ok ? hold + 1 : 0;
        if (hold >= HOLD_FRAMES) {
          hold = 0;
          step += 1;
          if (navigator.vibrate) { try { navigator.vibrate(25); } catch (e) { /* optional */ } }
          call('step', step, STEPS[step] || 'frontal');
          call('prompt', STEPS[step] || (best ? 'done' : 'frontal'));
        }
      }
      if (step >= STEPS.length) {
        if (best) finish();
        else call('prompt', 'frontal');
      }
    }

    function tick() {
      if (stopped) return;
      raf = requestAnimationFrame(tick);
      var t = now();
      if (!slowSent && t - started > SLOW_MS) { slowSent = true; call('slow'); }
      if (t - lastRun < FRAME_MS || video.readyState < 2 || !video.videoWidth) return;
      if (video.currentTime === lastVideoTime) return;
      lastVideoTime = video.currentTime;
      lastRun = t;
      // MediaPipe needs strictly increasing timestamps.
      var ts = Math.max(t, lastTs + 1);
      lastTs = ts;
      var result;
      try {
        result = lm.detectForVideo(video, ts);
        errors = 0;
      } catch (error) {
        errors += 1;
        if (errors > 5) { stopped = true; call('error', error); }
        return;
      }
      analyse(result, t);
    }

    call('step', 0, STEPS[0]);
    call('prompt', STEPS[0]);
    raf = requestAnimationFrame(tick);
    return {
      stop: function () { stopped = true; if (raf) cancelAnimationFrame(raf); }
    };
  }

  /** Keep the screen awake during the check (guarded; not every browser has it). */
  function wakeLock() {
    var lock = null, wanted = true;
    function get() {
      if (!wanted || !navigator.wakeLock || document.visibilityState !== 'visible') return;
      try {
        navigator.wakeLock.request('screen').then(function (l) {
          if (!wanted) { l.release(); return; }
          lock = l;
        }, function () { /* denied or unsupported */ });
      } catch (e) { /* unsupported */ }
    }
    function visible() { if (document.visibilityState === 'visible') get(); }
    document.addEventListener('visibilitychange', visible);
    get();
    return {
      release: function () {
        wanted = false;
        document.removeEventListener('visibilitychange', visible);
        if (lock) { try { lock.release(); } catch (e) { /* already gone */ } lock = null; }
      }
    };
  }

  var PROMPTS = {
    left: 'Turn your head to the left',
    right: 'Now turn your head to the right',
    smile: 'Now give a big smile',
    frontal: 'Look straight at the camera',
    done: 'Done — hold on'
  };
  var ARROWS = { left: '←', right: '→', smile: '😊', frontal: '•', done: '✓' };

  /** UI callbacks for the shared check-stage markup (_liveness_stage.html). */
  function stage(doc) {
    var $ = function (id) { return doc.getElementById(id); };
    var prompt = $('lv-prompt'), arrow = $('lv-arrow'), hintEl = $('lv-hint'), darkEl = $('lv-dark');
    var chips = doc.querySelectorAll('[data-lv-step]');
    function toggle(el, on) { if (el) el.classList.toggle('hidden', !on); }
    return {
      step: function (count) {
        for (var i = 0; i < chips.length; i++) {
          var done = i < count;
          chips[i].classList.toggle('done', done);
          chips[i].classList.toggle('current', i === count);
          if (i === count) chips[i].setAttribute('aria-current', 'step');
          else chips[i].removeAttribute('aria-current');
        }
      },
      prompt: function (name) {
        if (prompt) prompt.textContent = PROMPTS[name] || '';
        if (arrow) {
          arrow.textContent = ARROWS[name] || '';
          arrow.className = 'lv-arrow lv-' + name;
        }
      },
      hint: function (text) {
        if (hintEl) hintEl.textContent = text || '';
        toggle(hintEl, !!text);
      },
      dark: function (on) { toggle(darkEl, on); }
    };
  }

  window.ZitchLiveness = {
    STEPS: STEPS,
    load: load,
    session: session,
    snapshot: snapshot,
    stage: stage,
    wakeLock: wakeLock,
    // Exposed for tests of the geometry.
    _measure: measure
  };
})();
