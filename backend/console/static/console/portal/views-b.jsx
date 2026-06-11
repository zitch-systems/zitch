// Zitch Admin — views B: WhatsApp inbox, Broadcasts, AI controls, Audit log, Settings
// Conversation + broadcast actions POST to the audited /api/admin/wa/* endpoints
// (server-side RBAC: wa / broadcast capabilities) before patching local state.
const { useState } = React;
const DB = window.ZADM;

// ================= WHATSAPP INBOX =================
function WaInbox({ toast }) {
  const { can } = useRole();
  const [convos, setConvos] = useState(DB.CONVOS);
  const [selIdx, setSelIdx] = useState(0);
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);
  const c = convos[selIdx];

  if (!c) {
    return (
      <div>
        <PageHead title="WhatsApp" sub="Conversation monitor — while a chat is with a human, the bot stays silent." />
        <Card><Empty text="No conversations yet — they appear as soon as users message the business number." /></Card>
      </div>
    );
  }

  const patch = (idx, p) => {
    const next = convos.map((x, i) => (i === idx ? { ...x, ...p } : x));
    setConvos(next); DB.CONVOS = next;
  };

  const handover = async () => {
    setBusy(true);
    const r = await doAct(toast, '/wa/handover', { msisdn: c.msisdn, mode: 'human' });
    setBusy(false);
    if (r) { patch(selIdx, { status: 'human', aiEnabled: false, agent: 'You' }); toast('Bot paused — conversation handed to you (audit logged)'); }
  };
  const returnBot = async () => {
    setBusy(true);
    const r = await doAct(toast, '/wa/handover', { msisdn: c.msisdn, mode: 'bot' });
    setBusy(false);
    if (r) { patch(selIdx, { status: 'bot', aiEnabled: true, agent: null }); toast('Returned to bot (audit logged)'); }
  };
  const toggleAi = async (v) => {
    const r = await doAct(toast, '/wa/conv_ai', { msisdn: c.msisdn, enabled: v });
    if (r) { patch(selIdx, { aiEnabled: v }); toast('Conversation AI ' + (v ? 'enabled' : 'disabled') + ' (audit logged)'); }
  };
  const sendReply = async () => {
    if (!reply.trim() || busy) return;
    setBusy(true);
    const text = reply.trim();
    const r = await doAct(toast, '/wa/reply', { msisdn: c.msisdn, text });
    setBusy(false);
    if (r) {
      patch(selIdx, { msgs: [...c.msgs, { dir: 'out', text: '[Agent · You] ' + text, t: new Date(), agent: true }] });
      setReply(''); toast('Agent reply sent (audit logged)');
    }
  };

  return (
    <div>
      <PageHead title="WhatsApp" sub="Conversation monitor — while a chat is with a human, the bot stays silent." />
      <div className="wa-layout">
        <div className="card convo-list">
          {convos.map((cv, i) => (
            <button key={cv.msisdn} className={'convo' + (i === selIdx ? ' on' : '')} onClick={() => setSelIdx(i)}>
              <span className="avatar">{cv.user === '(unlinked)' ? '?' : cv.user.split(' ').map((w) => w[0]).join('')}</span>
              <span className="convo-mid">
                <b>{cv.user}</b>
                <span className="dim sm">{cv.msisdn}</span>
              </span>
              <span className="convo-end">
                <Badge v={cv.status} />
                <span className="dim sm num">{cv.last ? DB.fmtT(cv.last) : '—'}</span>
              </span>
            </button>
          ))}
        </div>
        <div className="card chat-pane">
          <div className="chat-pane-head">
            <div>
              <b>{c.user}</b> <span className="dim">{c.msisdn}</span>
              {c.agent && <span className="dim sm"> · with {c.agent}</span>}
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span className="sm dim">AI</span>
              <Toggle on={c.aiEnabled} disabled={!can.wa || c.status === 'human'} label="conversation AI"
                onChange={toggleAi} />
              {c.status === 'human'
                ? <button className="btn ghost sm-btn" disabled={!can.wa || busy} onClick={returnBot}><Icon name="bot" size={14} /> Return to bot</button>
                : <button className="btn primary sm-btn" disabled={!can.wa || busy} onClick={handover}><Icon name="user" size={14} /> Take over</button>}
            </div>
          </div>
          <div className="chat-scroll">
            {c.msgs.map((m, i) => (
              <div key={i} className={'bubble-row' + (m.dir === 'in' ? '' : ' out')}>
                <div className={'bubble' + (m.dir === 'in' ? ' in' : m.agent ? ' agent' : ' bot')}>
                  {m.text}
                  {m.flagged && <span className="flag-tag"><Icon name="alert" size={11} /> flagged</span>}
                  {m.intent && (
                    <div className="intent num">⚙ {JSON.stringify(m.intent)}</div>
                  )}
                </div>
              </div>
            ))}
          </div>
          <div className="chat-input">
            {c.status === 'human' ? (
              <React.Fragment>
                <input value={reply} disabled={!can.wa || busy} onChange={(e) => setReply(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && sendReply()} placeholder="Reply as agent…" />
                <button className="btn primary sm-btn" disabled={!can.wa || busy} onClick={sendReply}><Icon name="send" size={14} /> {busy ? 'Sending…' : 'Send'}</button>
              </React.Fragment>
            ) : (
              <div className="dim sm" style={{ padding: '4px 2px' }}><Icon name="bot" size={13} /> Bot is handling this conversation. Take over to reply as an agent.</div>
            )}
          </div>
          {!can.wa && <p className="rbac-note" style={{ padding: '0 16px 12px' }}><Icon name="lock" size={13} /> Support or super admin only.</p>}
        </div>
      </div>
    </div>
  );
}

// ================= BROADCASTS =================
const BROADCAST_TEMPLATES = ['fx_rate_drop_alert', 'june_cashback_promo', 'maintenance_window', 'savings_rate_update'];

function Broadcasts({ toast }) {
  const { can } = useRole();
  const [rows, setRows] = useState(DB.BROADCASTS);
  const [cat, setCat] = useState('utility');
  const [tpl, setTpl] = useState(BROADCAST_TEMPLATES[0]);
  const [busy, setBusy] = useState(false);
  const k = DB.KPIS || {};
  const optedIn = k.wa_optin || 0, linked = k.wa_links || 0;

  const queue = async () => {
    setBusy(true);
    const r = await doAct(toast, '/wa/broadcast', { template_name: tpl, category: cat });
    setBusy(false);
    if (r && r.broadcast) {
      const next = [r.broadcast, ...rows];
      setRows(next); DB.BROADCASTS = next;
      toast('Broadcast sent — ' + r.broadcast.queued + ' queued, ' + r.broadcast.sent + ' delivered to provider (audit logged)');
    }
  };

  return (
    <div>
      <PageHead title="Broadcasts" sub="Template campaigns over WhatsApp. STOP / UNSUBSCRIBE flips marketing opt-in off automatically." />
      <div className="grid-2-1">
        <Card title="Campaigns" pad={false}>
          {rows.length ? (
            <table className="tbl">
              <thead><tr><th>Template</th><th>Category</th><th>Status</th><th className="r">Queued</th><th className="r">Delivered</th><th className="r">Read</th><th className="r">Failed</th></tr></thead>
              <tbody>
                {rows.map((b) => (
                  <tr key={b.id}>
                    <td><b className="mono">{b.template}</b><div className="sm dim">{b.created} · {b.by}</div></td>
                    <td><Badge v={b.category === 'marketing' ? 'human' : 'bot'}>{b.category}</Badge></td>
                    <td><Badge v={b.status} /></td>
                    <td className="r num">{(b.queued || 0).toLocaleString()}</td>
                    <td className="r num">{(b.delivered || 0).toLocaleString()}</td>
                    <td className="r num">{(b.read || 0).toLocaleString()}</td>
                    <td className="r num">{(b.failed || 0).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty text="No campaigns yet." />}
        </Card>
        <Card title="New broadcast" sub="Meta-approved templates only">
          <label className="f-label">Template</label>
          <select className="f-input" value={tpl} onChange={(e) => setTpl(e.target.value)}>
            {BROADCAST_TEMPLATES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <label className="f-label">Category</label>
          <div className="chips" style={{ marginBottom: 4 }}>
            {['utility', 'marketing'].map((x) => <button key={x} className={'chip' + (cat === x ? ' on' : '')} onClick={() => setCat(x)}>{x}</button>)}
          </div>
          <div className={'note ' + (cat === 'marketing' ? 'warn' : '')}>
            {cat === 'marketing'
              ? <React.Fragment><Icon name="alert" size={14} /> Marketing reaches only the <b>{optedIn.toLocaleString()}</b> opted-in users.</React.Fragment>
              : <React.Fragment><Icon name="check" size={14} /> Utility reaches all <b>{linked.toLocaleString()}</b> linked numbers.</React.Fragment>}
          </div>
          <button className="btn primary w100" style={{ marginTop: 14 }} disabled={!can.broadcast || busy} onClick={queue}>
            <Icon name="megaphone" size={15} /> {busy ? 'Sending…' : 'Queue broadcast'}
          </button>
          {!can.broadcast && <p className="rbac-note"><Icon name="lock" size={13} /> Support or super admin only.</p>}
        </Card>
      </div>
    </div>
  );
}

// ================= AI CONTROLS =================
function AiControls({ toast }) {
  const { can } = useRole();
  const initial = (DB.SETTINGS.find((s) => s.key === 'ai_enabled_global') || {}).value;
  const [global, setGlobal] = useState(String(initial) !== 'false');
  const [saving, setSaving] = useState(false);
  const intents = DB.CONVOS.flatMap((c) => c.msgs.filter((m) => m.intent).map((m) => ({ ...m, user: c.user, msisdn: c.msisdn })));

  // Persist the kill switch to SystemSetting via the audited staff endpoint.
  const setKill = async (v) => {
    if (saving) return;
    setSaving(true);
    setGlobal(v); // optimistic
    try {
      await ZADM_API.act('/settings/update', { key: 'ai_enabled_global', value: v ? 'true' : 'false' });
      toast('ai_enabled_global = ' + v + ' (audit logged)');
    } catch (e) {
      setGlobal(!v); // revert on failure
      toast(e.message || 'Could not update setting');
    } finally { setSaving(false); }
  };
  return (
    <div>
      <PageHead title="AI controls" sub="The model only proposes — validation, confirm and PIN still gate every movement." />
      <div className="grid-2-1">
        <Card title="Recent parsed intents" sub="Stored on each inbound message" pad={false}>
          {intents.length ? (
            <table className="tbl">
              <thead><tr><th>User</th><th>Message</th><th>Parsed intent</th><th className="r">Confidence</th></tr></thead>
              <tbody>
                {intents.map((m, i) => (
                  <tr key={i}>
                    <td><b>{m.user}</b><div className="sm dim">{m.msisdn}</div></td>
                    <td className="dim">“{m.text}”</td>
                    <td><span className="mono sm">{m.intent.intent}</span></td>
                    <td className="r num">{m.intent.confidence ? Math.round(m.intent.confidence * 100) + '%' : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty text="No parsed intents yet — they appear when the AI layer handles free-form messages." />}
        </Card>
        <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
          <Card title="Global kill switch" sub="SystemSetting · ai_enabled_global">
            <div className="kill">
              <div>
                <b>{global ? 'AI intent layer is ON' : 'AI intent layer is OFF'}</b>
                <p className="dim sm" style={{ margin: '4px 0 0' }}>{global ? 'Free-form text is mapped to intents by Claude (temp 0, tool calling).' : 'Channel is fully menu-driven. Keywords and numbered menus still work.'}</p>
              </div>
              <Toggle on={global} disabled={!can.ai || saving} label="global AI"
                onChange={setKill} />
            </div>
            {!can.ai && <p className="rbac-note"><Icon name="lock" size={13} /> Super admin only.</p>}
          </Card>
          <Card title="How scoping works">
            <div className="rules">
              <div className="rule"><Icon name="check" size={15} /> AI runs only when global AND per-user AND per-conversation toggles are all on.</div>
              <div className="rule"><Icon name="check" size={15} /> Explicit keywords, menus and paste always run deterministically first.</div>
              <div className="rule"><Icon name="check" size={15} /> Handover to a human auto-disables AI for that conversation.</div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

// ================= AUDIT LOG =================
function Audit() {
  const [q, setQ] = useState('');
  const rows = DB.AUDIT.filter((a) => q === '' || (a.actor + a.action + a.target).toLowerCase().includes(q.toLowerCase()));
  return (
    <div>
      <PageHead title="Audit log" sub="Append-only. Every admin action and sensitive event, with before/after." right={<SearchBox value={q} onChange={setQ} placeholder="Search actor, action…" />} />
      <Card pad={false}>
        {rows.length ? (
          <table className="tbl">
            <thead><tr><th>Actor</th><th>Action</th><th>Target</th><th>Before → After</th><th className="r">When</th></tr></thead>
            <tbody>
              {rows.map((a, i) => (
                <tr key={i}>
                  <td><b>{a.actor}</b><div className="sm dim">{a.role}</div></td>
                  <td><span className="mono sm">{a.action}</span></td>
                  <td className="dim">{a.target}</td>
                  <td><span className="mono sm dim">{JSON.stringify(a.before)} → {JSON.stringify(a.after)}</span></td>
                  <td className="r dim num">{DB.fmtT(a.t)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty text="No audit entries match." />}
      </Card>
    </div>
  );
}

// ================= SETTINGS & TEAM =================
function Settings({ toast }) {
  const { can } = useRole();
  return (
    <div>
      <PageHead title="Settings & team" sub="Runtime configuration and role-based access." />
      <div className="grid-2-1">
        <Card title="System settings" sub="SystemSetting key/value — money-sensitive keys change via their own audited endpoints" pad={false}>
          <table className="tbl">
            <thead><tr><th>Key</th><th>Value</th><th>Description</th></tr></thead>
            <tbody>
              {DB.SETTINGS.map((s) => (
                <tr key={s.key}>
                  <td><span className="mono sm">{s.key}</span></td>
                  <td><b className="num">{s.value}</b></td>
                  <td className="dim sm">{s.desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Team" sub={DB.TEAM.length + ' member' + (DB.TEAM.length === 1 ? '' : 's')}>
          {DB.TEAM.map((m) => (
            <div key={m.email} className="kv tight">
              <span><b>{m.name}</b> <em className="dim sm">{m.email}</em></span>
              <Badge v={m.role === 'super_admin' ? 'success' : m.role === 'finance' ? 'human' : m.role === 'support' ? 'bot' : 'draft'}>{m.role}</Badge>
            </div>
          ))}
          <p className="dim sm" style={{ marginTop: 12 }}>
            <Icon name="lock" size={13} /> Staff accounts and role groups (finance / support / read_only) are managed in Django admin.
          </p>
        </Card>
      </div>
      <Card title="Role permissions" sub="What each role can do — enforced server-side on every endpoint" pad={false}>
        <table className="tbl">
          <thead><tr><th>Permission</th>{ROLES.map((r) => <th key={r} className="r">{r}</th>)}</tr></thead>
          <tbody>
            {DB.PERMS.map((p) => (
              <tr key={p.perm}>
                <td>{p.perm}</td>
                {ROLES.map((r) => (
                  <td key={r} className="r">{p[r] ? <span className="perm yes"><Icon name="check" size={14} /></span> : <span className="perm no"><Icon name="x" size={13} /></span>}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

Object.assign(window, { WaInbox, Broadcasts, AiControls, Audit, Settings });
