/**
 * Crystal companion orb — midnight translucent core, luminous infinity-flow
 * ribbons (icy blue / white / lavender / peach), thin ring, soft halo.
 * Phase-reactive; not a cement ball, neon orb, or stripe junk.
 */
(function () {
  const canvas = document.getElementById("orbCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const orbBtn = document.getElementById("orb");
  const transport = document.querySelector(".transport");
  const progressFill = document.getElementById("progressFill");

  const DPR = Math.min(window.devicePixelRatio || 1, 2);
  let w = 360;
  let h = 360;
  let phase = "idle";
  let t0 = performance.now();

  function resize() {
    const rect = canvas.getBoundingClientRect();
    w = Math.max(160, Math.floor(rect.width));
    h = Math.max(160, Math.floor(rect.height));
    canvas.width = w * DPR;
    canvas.height = h * DPR;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }

  function palette() {
    if (phase === "listening") {
      return {
        coreDeep: [8, 16, 42],
        coreMid: [28, 52, 110],
        glow: [90, 170, 255],
        ribbonA: [180, 230, 255],
        ribbonB: [120, 160, 255],
        ribbonC: [200, 180, 255],
        peach: [255, 210, 190],
        ring: [170, 210, 255],
        energy: 1.15,
      };
    }
    if (phase === "thinking") {
      return {
        coreDeep: [12, 14, 48],
        coreMid: [40, 40, 120],
        glow: [150, 140, 255],
        ribbonA: [200, 190, 255],
        ribbonB: [130, 150, 255],
        ribbonC: [180, 160, 255],
        peach: [255, 205, 195],
        ring: [180, 170, 255],
        energy: 0.95,
      };
    }
    if (phase === "speaking") {
      return {
        coreDeep: [8, 22, 48],
        coreMid: [30, 70, 120],
        glow: [100, 190, 240],
        ribbonA: [190, 240, 255],
        ribbonB: [140, 190, 255],
        ribbonC: [210, 185, 255],
        peach: [255, 215, 185],
        ring: [160, 210, 255],
        energy: 1.25,
      };
    }
    return {
      coreDeep: [10, 18, 40],
      coreMid: [32, 55, 105],
      glow: [120, 165, 240],
      ribbonA: [200, 230, 255],
      ribbonB: [140, 170, 255],
      ribbonC: [190, 175, 255],
      peach: [255, 215, 195],
      ring: [165, 195, 255],
      energy: 0.82,
    };
  }

  function rgba(c, a) {
    return `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  }

  /** Soft infinity / lemniscate ribbon path */
  function drawRibbon(cx, cy, R, t, opts) {
    const {
      scale = 0.55,
      rot = 0,
      width = 0.085,
      color,
      alpha = 0.45,
      speed = 1,
      phaseOff = 0,
    } = opts;
    const steps = 96;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(rot);
    ctx.globalCompositeOperation = "lighter";

    // Glow pass
    ctx.beginPath();
    for (let i = 0; i <= steps; i++) {
      const u = (i / steps) * Math.PI * 2 + t * 0.55 * speed + phaseOff;
      const s = Math.sin(u);
      const c = Math.cos(u);
      // Lemniscate of Bernoulli (parametric)
      const denom = 1 + s * s;
      const x = (R * scale * c) / denom;
      const y = (R * scale * s * c) / denom;
      const wobble =
        Math.sin(u * 2.2 + t * 0.7) * R * 0.018 +
        Math.cos(u * 3.1 - t * 0.5) * R * 0.012;
      const px = x + wobble * Math.cos(u);
      const py = y + wobble * Math.sin(u);
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    }
    ctx.strokeStyle = rgba(color, alpha * 0.35);
    ctx.lineWidth = R * width * 2.4;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.stroke();

    // Bright core of ribbon
    ctx.beginPath();
    for (let i = 0; i <= steps; i++) {
      const u = (i / steps) * Math.PI * 2 + t * 0.55 * speed + phaseOff;
      const s = Math.sin(u);
      const c = Math.cos(u);
      const denom = 1 + s * s;
      const x = (R * scale * c) / denom;
      const y = (R * scale * s * c) / denom;
      const wobble =
        Math.sin(u * 2.2 + t * 0.7) * R * 0.018 +
        Math.cos(u * 3.1 - t * 0.5) * R * 0.012;
      const px = x + wobble * Math.cos(u);
      const py = y + wobble * Math.sin(u);
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    }
    const grad = ctx.createLinearGradient(-R * scale, 0, R * scale, 0);
    grad.addColorStop(0, rgba(color, 0));
    grad.addColorStop(0.25, rgba(color, alpha));
    grad.addColorStop(0.5, `rgba(255,255,255,${Math.min(0.85, alpha + 0.25)})`);
    grad.addColorStop(0.75, rgba(color, alpha));
    grad.addColorStop(1, rgba(color, 0));
    ctx.strokeStyle = grad;
    ctx.lineWidth = R * width;
    ctx.stroke();
    ctx.restore();
  }

  function drawFlowArc(cx, cy, R, t, color, alpha, tilt, radiusScale) {
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.translate(cx, cy);
    ctx.rotate(tilt + Math.sin(t * 0.35) * 0.15);
    const rr = R * radiusScale;
    ctx.beginPath();
    ctx.ellipse(0, 0, rr, rr * 0.38, 0, 0.2, Math.PI * 1.6);
    ctx.strokeStyle = rgba(color, alpha);
    ctx.lineWidth = R * 0.045;
    ctx.lineCap = "round";
    ctx.stroke();
    ctx.beginPath();
    ctx.ellipse(0, 0, rr, rr * 0.38, 0, 0.4, Math.PI * 1.35);
    ctx.strokeStyle = `rgba(255,255,255,${alpha * 0.55})`;
    ctx.lineWidth = R * 0.018;
    ctx.stroke();
    ctx.restore();
  }

  function draw(now) {
    const t = (now - t0) / 1000;
    const cx = w / 2;
    const cy = h / 2;
    const p = palette();
    const breath =
      phase === "listening"
        ? 1 + Math.sin(t * 2.4) * 0.028
        : phase === "speaking"
          ? 1 + Math.sin(t * 3.2) * 0.022
          : phase === "thinking"
            ? 1 + Math.sin(t * 1.4) * 0.016
            : 1 + Math.sin(t * 0.85) * 0.012;
    const R = Math.min(w, h) * 0.32 * breath * (0.96 + p.energy * 0.04);

    ctx.clearRect(0, 0, w, h);

    // Soft ethereal outer halo
    const halo = ctx.createRadialGradient(cx, cy, R * 0.2, cx, cy, R * 2.05);
    halo.addColorStop(0, rgba(p.glow, 0.28 * p.energy));
    halo.addColorStop(0.35, rgba(p.ribbonC, 0.12));
    halo.addColorStop(0.55, rgba(p.peach, 0.06));
    halo.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(cx, cy, R * 2.05, 0, Math.PI * 2);
    ctx.fill();

    // Thin sharp glowing ring
    ctx.beginPath();
    ctx.arc(cx, cy, R * 1.08, 0, Math.PI * 2);
    ctx.strokeStyle = rgba(p.ring, 0.55);
    ctx.lineWidth = Math.max(1, R * 0.012);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, cy, R * 1.08, 0, Math.PI * 2);
    ctx.strokeStyle = rgba(p.glow, 0.2);
    ctx.lineWidth = Math.max(2, R * 0.035);
    ctx.stroke();

    // Clip to sphere for crystal interior
    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.clip();

    // Midnight translucent core
    const body = ctx.createRadialGradient(
      cx - R * 0.28,
      cy - R * 0.32,
      R * 0.02,
      cx,
      cy,
      R * 1.05
    );
    body.addColorStop(0, "rgba(230, 240, 255, 0.55)");
    body.addColorStop(0.18, rgba(p.coreMid, 0.55));
    body.addColorStop(0.48, rgba(p.coreDeep, 0.88));
    body.addColorStop(0.78, `rgba(${Math.max(0, p.coreDeep[0] - 4)},${Math.max(0, p.coreDeep[1] - 4)},${Math.max(0, p.coreDeep[2] - 8)},0.96)`);
    body.addColorStop(1, "rgba(4, 8, 22, 0.98)");
    ctx.fillStyle = body;
    ctx.fillRect(cx - R, cy - R, R * 2, R * 2);

    // Inner volume haze
    const inner = ctx.createRadialGradient(cx, cy + R * 0.05, R * 0.05, cx, cy, R * 0.95);
    inner.addColorStop(0, rgba(p.glow, 0.18));
    inner.addColorStop(0.45, rgba(p.ribbonC, 0.1));
    inner.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = inner;
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.fill();

    // Luminous infinity-flow ribbons
    drawRibbon(cx, cy - R * 0.02, R, t, {
      scale: 0.62,
      rot: -0.35 + Math.sin(t * 0.2) * 0.08,
      width: 0.09,
      color: p.ribbonA,
      alpha: 0.55 * p.energy,
      speed: 1,
      phaseOff: 0,
    });
    drawRibbon(cx, cy + R * 0.04, R, t, {
      scale: 0.48,
      rot: 0.9 + Math.cos(t * 0.18) * 0.1,
      width: 0.07,
      color: p.ribbonB,
      alpha: 0.42 * p.energy,
      speed: 0.85,
      phaseOff: 1.2,
    });
    drawRibbon(cx, cy, R, t, {
      scale: 0.38,
      rot: 2.1,
      width: 0.055,
      color: p.ribbonC,
      alpha: 0.38 * p.energy,
      speed: 1.15,
      phaseOff: 2.4,
    });

    // Soft peach accent arc
    drawFlowArc(cx, cy, R, t, p.peach, 0.22 * p.energy, 0.6, 0.72);
    drawFlowArc(cx, cy, R, t * 0.9, p.ribbonA, 0.2 * p.energy, -0.8, 0.58);

    // Specular catch-light (crystal)
    const sx = cx - R * 0.32 + Math.sin(t * 0.3) * R * 0.04;
    const sy = cy - R * 0.36 + Math.cos(t * 0.25) * R * 0.03;
    const spec = ctx.createRadialGradient(sx, sy, 0, sx, sy, R * 0.42);
    spec.addColorStop(0, "rgba(255,255,255,0.78)");
    spec.addColorStop(0.35, "rgba(220, 235, 255, 0.28)");
    spec.addColorStop(1, "rgba(255,255,255,0)");
    ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = spec;
    ctx.beginPath();
    ctx.ellipse(sx, sy, R * 0.28, R * 0.16, -0.55, 0, Math.PI * 2);
    ctx.fill();

    // Lower caustic whisper
    const cx2 = cx + R * 0.2;
    const cy2 = cy + R * 0.28;
    const caustic = ctx.createRadialGradient(cx2, cy2, 0, cx2, cy2, R * 0.35);
    caustic.addColorStop(0, rgba(p.peach, 0.16));
    caustic.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = caustic;
    ctx.beginPath();
    ctx.ellipse(cx2, cy2, R * 0.26, R * 0.14, 0.4, 0, Math.PI * 2);
    ctx.fill();

    ctx.restore(); // end sphere clip

    // Glass rim fresnel outside clip
    const rim = ctx.createRadialGradient(cx, cy, R * 0.82, cx, cy, R * 1.02);
    rim.addColorStop(0, "rgba(255,255,255,0)");
    rim.addColorStop(0.7, "rgba(200, 220, 255, 0.08)");
    rim.addColorStop(1, "rgba(255,255,255,0.35)");
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.fillStyle = rim;
    ctx.fill();

    // Crisp outer edge
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(255,255,255,0.28)";
    ctx.lineWidth = 1;
    ctx.stroke();

    requestAnimationFrame(draw);
  }

  const mo = new MutationObserver(() => {
    const next =
      ["idle", "listening", "thinking", "speaking"].find((p) => orbBtn.classList.contains(p)) ||
      "idle";
    phase = next;
    if (transport) {
      transport.classList.remove("is-idle", "is-listening", "is-thinking", "is-speaking");
      transport.classList.add(`is-${next}`);
    }
  });
  mo.observe(orbBtn, { attributes: true, attributeFilter: ["class"] });
  if (transport) transport.classList.add("is-idle");

  window.addEventListener("resize", resize);
  resize();
  requestAnimationFrame(draw);

  window.__voxorylOrb = {
    setProgress(pct) {
      if (progressFill) progressFill.style.width = `${Math.max(6, Math.min(98, pct))}%`;
    },
  };
})();
