// shared.jsx — Zitch revamp: icon set, ribbon logo, status bar, helpers
// Exports to window: ZIcon, ZMark, ZWordmark, StatusBar, HomeBar, Avatar
(function () {
  const P = {
    bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    eyeoff: '<path d="M10.7 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a13 13 0 0 1-1.7 2.4M6.6 6.6A13 13 0 0 0 2 12s3.5 7 10 7a10 10 0 0 0 3.4-.6"/><path d="m9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="m2 2 20 20"/>',
    scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M3 12h18"/>',
    deposit: '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
    withdraw: '<path d="M12 21V9"/><path d="m7 14 5-5 5 5"/><path d="M5 3h14"/>',
    send: '<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4 20-7Z"/>',
    airtime: '<rect x="5" y="2" width="14" height="20" rx="2.5"/><path d="M11 18h2"/>',
    data: '<path d="M5 12.5a11 11 0 0 1 14 0M8.5 16a6 6 0 0 1 7 0M2 9a15 15 0 0 1 20 0"/><path d="M12 20h.01"/>',
    bills: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8Z"/>',
    loan: '<circle cx="9" cy="9" r="6.2"/><path d="M18.1 10.4A6 6 0 1 1 10.4 18"/><path d="M8.3 6.6h1.6a1.4 1.4 0 0 1 0 2.8H8.3h1.7a1.4 1.4 0 0 1 0 2.8H8.3M9.4 5.6v8"/>',
    movie: '<path d="M3 11h18v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M20.2 6 3 11l-.9-2.4c-.3-1.1.3-2.2 1.3-2.5l13.5-4c1.1-.3 2.2.3 2.5 1.3Z"/><path d="m6.2 5.3 3.1 3.9M12.4 3.4l3.1 4"/>',
    insurance: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/>',
    remita: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/><path d="M16 13H8M16 17H8M10 9H8"/>',
    jamb: '<path d="M22 10 12 5 2 10l10 5 10-5Z"/><path d="M6 12v5c3 2.7 9 2.7 12 0v-5"/>',
    save: '<path d="M2 11 12 4l10 7"/><path d="M4 10v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="M9 20v-5h6v5"/>',
    convert: '<path d="m17 2 4 4-4 4"/><path d="M3 11V10a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    more: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    home: '<path d="m3 10 9-7 9 7v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M9 21v-7h6v7"/>',
    wallet: '<path d="M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-2"/><path d="M21 12a2 2 0 0 0-2-2h-4a2 2 0 0 0 0 8h4a2 2 0 0 0 2-2Z"/><path d="M17 14h.01"/>',
    chart: '<path d="M3 3v18h18"/><path d="M7 16v-5M12 16V8M17 16v-3"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 20a8 8 0 0 1 16 0"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    right: '<path d="m9 18 6-6-6-6"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    up: '<path d="m18 15-6-6-6 6"/>',
    left: '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    ticket: '<path d="M3 9a3 3 0 0 0 0 6v2a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2Z"/><path d="M13 5v2M13 11v2M13 17v2"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    spark: '<path d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9Z"/>',
    gift: '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7"/><path d="M12 8S10.5 3 7.5 3 5 6 5 6s2 2 7 2ZM12 8s1.5-5 4.5-5S19 6 19 6s-2 2-7 2Z"/>',
    arrowR: '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2"/>',
    share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4"/>',
    download: '<path d="M12 3v12"/><path d="m7 11 5 4 5-4"/><path d="M5 21h14"/>',
    history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 4v4h4"/><path d="M12 8v4l3 2"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 6.8 19l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 3 13.3H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 6.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.6 1.6 0 0 0 10 4.6V4a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0 1.1 2.7H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1Z"/>',
    qr: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3M21 14v.01M14 21h.01M21 17v4h-4"/>',
    lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    help: '<path d="M3 14v-2a9 9 0 0 1 18 0v2"/><path d="M21 15.5a2 2 0 0 1-2 2h-1v-6h1a2 2 0 0 1 2 2Z"/><path d="M3 15.5a2 2 0 0 0 2 2h1v-6H5a2 2 0 0 0-2 2Z"/>',
    tv: '<rect x="3" y="7" width="18" height="13" rx="2.5"/><path d="m8 3 4 4 4-4"/>',
    dice: '<rect x="4" y="4" width="16" height="16" rx="3.5"/><circle cx="9" cy="9" r="1.25" fill="currentColor" stroke="none"/><circle cx="15" cy="15" r="1.25" fill="currentColor" stroke="none"/><circle cx="15" cy="9" r="1.25" fill="currentColor" stroke="none"/><circle cx="9" cy="15" r="1.25" fill="currentColor" stroke="none"/>',
    bank: '<path d="M3 10 12 4l9 6"/><path d="M5 10v9M19 10v9M9.5 10v9M14.5 10v9"/><path d="M3 21h18"/>',
    card: '<rect x="2" y="5" width="20" height="14" rx="2.5"/><path d="M2 10h20"/><path d="M6 15h4"/>',
    fixed: '<rect x="3" y="8" width="18" height="13" rx="2.5"/><path d="M8 8V6a4 4 0 0 1 8 0v2"/><path d="M12 13v3"/>',
    invite: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M19 8v6M22 11h-6"/>',
    fingerprint: '<path d="M12 10a2 2 0 0 0-2 2c0 1.6-.4 3.2-1.1 4.6"/><path d="M12 6.5A5.5 5.5 0 0 1 17.5 12c0 2-.3 3.6-.9 5.2"/><path d="M9.2 18.8c.5-1 1-2.6 1-4.8a1.8 1.8 0 0 1 3.6 0c0 1.1-.1 2.1-.4 3.1"/><path d="M6 14.5c.4-1 .6-2 .6-3A5.4 5.4 0 0 1 12 6c1.1 0 2.1.3 3 .8"/><path d="M3.6 11.5A8.5 8.5 0 0 1 8 4.8"/>',
    faceid: '<path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><path d="M9 10v1M15 10v1M12 9.5v3l-1 .8"/><path d="M9.3 15a3.6 3.6 0 0 0 5.4 0"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
  };

  function ZIcon({ name, size = 22, stroke = 1.75, color = 'currentColor', style }) {
    return React.createElement('svg', {
      width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
      stroke: color, strokeWidth: stroke, strokeLinecap: 'round', strokeLinejoin: 'round',
      style, dangerouslySetInnerHTML: { __html: P[name] || '' },
    });
  }

  // Real Zitch logo — ribbon mark (transparent) by default, circular badge via `badge`
  function ZMark({ size = 40, glow = false, badge = false }) {
    if (badge) {
      return React.createElement('img', { src: '/static/console/assets/brand/zitch-badge.png', width: size, height: size, alt: 'Zitch',
        style: { borderRadius: '50%', objectFit: 'cover', display: 'block',
          filter: glow ? 'drop-shadow(0 10px 26px rgba(92,245,235,.45))' : 'none' } });
    }
    return React.createElement('img', { src: '/static/console/assets/brand/zitch-mark.png', width: size, height: 'auto', alt: 'Zitch',
      style: { display: 'block',
        filter: glow ? 'drop-shadow(0 6px 18px rgba(92,245,235,.5))' : 'none' } });
  }

  function ZWordmark({ size = 20, color = 'currentColor' }) {
    return React.createElement('span', { style: {
      fontFamily: 'var(--font)', fontWeight: 700, fontSize: size, letterSpacing: '.16em',
      color, lineHeight: 1 } }, 'ZITCH');
  }

  function StatusBar({ color = 'var(--ink-1)' }) {
    return React.createElement('div', { style: {
      height: 50, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '0 26px 0 28px', color, flexShrink: 0 } },
      React.createElement('span', { style: { fontWeight: 600, fontSize: 16, letterSpacing: '.01em' } }, '9:41'),
      React.createElement('div', { style: { display: 'flex', gap: 7, alignItems: 'center' } },
        React.createElement('svg', { width: 18, height: 12, viewBox: '0 0 18 12', fill: color },
          React.createElement('rect', { x: 0, y: 7, width: 3, height: 5, rx: 1 }),
          React.createElement('rect', { x: 5, y: 4.5, width: 3, height: 7.5, rx: 1 }),
          React.createElement('rect', { x: 10, y: 2, width: 3, height: 10, rx: 1 }),
          React.createElement('rect', { x: 15, y: 0, width: 3, height: 12, rx: 1 })),
        React.createElement('svg', { width: 17, height: 12, viewBox: '0 0 17 12', fill: color },
          React.createElement('path', { d: 'M8.5 2.5c2.3 0 4.4.9 6 2.4l-1.2 1.3a6.8 6.8 0 0 0-9.6 0L2.5 4.9A8.8 8.8 0 0 1 8.5 2.5Zm0 3.6c1.3 0 2.6.5 3.5 1.5l-1.3 1.3a3.1 3.1 0 0 0-4.4 0L5 7.6a4.9 4.9 0 0 1 3.5-1.5Zm0 3.4 1.5 1.5-1.5 1.5L7 11l1.5-1.5Z' })),
        React.createElement('svg', { width: 26, height: 13, viewBox: '0 0 26 13', fill: 'none' },
          React.createElement('rect', { x: .5, y: .5, width: 21, height: 12, rx: 3.5, stroke: color, opacity: .4 }),
          React.createElement('rect', { x: 2, y: 2, width: 18, height: 9, rx: 2, fill: color }),
          React.createElement('rect', { x: 23, y: 4, width: 2, height: 5, rx: 1, fill: color, opacity: .5 }))));
  }

  function HomeBar({ color = 'var(--ink-1)' }) {
    return React.createElement('div', { style: { height: 26, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 } },
      React.createElement('div', { style: { width: 134, height: 5, borderRadius: 3, background: color, opacity: .9 } }));
  }

  function Avatar({ size = 44, ring }) {
    return React.createElement('div', { style: {
      width: size, height: size, borderRadius: '50%', flexShrink: 0,
      background: 'linear-gradient(135deg,#FFD27A,#FF9F6B)',
      boxShadow: ring ? '0 0 0 2px var(--surface), 0 0 0 4px ' + ring : 'none',
      display: 'flex', alignItems: 'flex-end', justifyContent: 'center', overflow: 'hidden' } },
      React.createElement('svg', { width: size * .8, height: size * .8, viewBox: '0 0 40 40' },
        React.createElement('circle', { cx: 20, cy: 15, r: 7, fill: '#5B3A29' }),
        React.createElement('path', { d: 'M6 40c0-8 6.3-13 14-13s14 5 14 13Z', fill: '#7A4B33' })));
  }

  Object.assign(window, { ZIcon, ZMark, ZWordmark, StatusBar, HomeBar, Avatar });
})();
