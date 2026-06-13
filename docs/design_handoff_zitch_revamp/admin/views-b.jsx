// Zitch Admin — views B: WhatsApp inbox, Broadcasts, AI controls, Audit log, Settings
const { useState } = React;
const DB = window.ZADM;

// ================= WHATSAPP INBOX =================
function WaInbox({ toast }) {
  const { can } = useRole();
  const [convos, setConvos] = useState(DB.CONVOS);
  const [selIdx, setSelIdx] = useState(0);
  const [reply, setReply] = useState('');
  const c = convos[selIdx];

  const patch = (idx, p) => setConvos(convos.map((x, i) => (i === idx ? { ...x, ...p } : x)));

  const handover = () => { patch(selIdx, { status: 'human', aiEnabled: false, agent: 'You' }); toast('Bot paused — conversation handed to you (audit logged)'); };
  const returnBot = () => { patch(selIdx, { status: 'bot', aiEnabled: true, agent: null }); toast('Returned to bot (audit logged)'); };
  const sendReply = () => {
    if (!reply.trim()) return;
    patch(selIdx, { msgs: [...c.msgs, { dir: 'out', text: '[Agent · You] ' + reply, t: new Date(), agent: true }] });
    setReply(''); toast('Agent reply sent (audit logged)');
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
                <span className="dim sm num">{DB.fmtT(cv.last)}</span>
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
                onChange={(v) => { patch(selIdx, { aiEnabled: v }); toast('Conversation AI ' + (v ? 'enabled' : 'disabled')); }} />
              {c.status === 'human'
                ? <button className="btn ghost sm-btn" disabled={!can.wa} onClick={returnBot}><Icon name="bot" size={14} /> Return to bot</button>
                : <button className="btn primary sm-btn" disabled={!can.wa} onClick={handover}><Icon name="user" size={14} /> Take over</button>}
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
                <input value={reply} disabled={!can.wa} onChange={(e) => setReply(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && sendReply()} placeholder="Reply as agent…" />
                <button className="btn primary sm-btn" disabled={!can.wa} onClick={sendReply}><Icon name="send" size={14} /> Send</button>
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
function Broadcasts({ toast }) {
  const { can } = useRole();
  const [cat, setCat] = useState('utility');
  const optedIn = 1874, linked = 3098;
  return (
    <div>
      <PageHead title="Broadcasts" sub="Template campaigns over WhatsApp. STOP / UNSUBSCRIBE flips marketing opt-in off automatically." />
      <div className="grid-2-1">
        <Card title="Campaigns" pad={false}>
          <table className="tbl">
            <thead><tr><th>Template</th><th>Category</th><th>Status</th><th className="r">Queued</th><th className="r">Delivered</th><th className="r">Read</th><th className="r">Failed</th></tr></thead>
            <tbody>
              {DB.BROADCASTS.map((b) => (
                <tr key={b.id}>
                  <td><b className="mono">{b.template}</b><div className="sm dim">{b.created} · {b.by}</div></td>
                  <td><Badge v={b.category === 'marketing' ? 'human' : 'bot'}>{b.category}</Badge></td>
                  <td><Badge v={b.status} /></td>
                  <td className="r num">{b.queued.toLocaleString()}</td>
                  <td className="r num">{b.delivered.toLocaleString()}</td>
                  <td className="r num">{b.read.toLocaleString()}</td>
                  <td className="r num">{b.failed.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="New broadcast" sub="Meta-approved templates only">
          <label className="f-label">Template</label>
          <select className="f-input">
            <option>fx_rate_drop_alert</option>
            <option>june_cashback_promo</option>
            <option>maintenance_window</option>
            <option>savings_rate_update</option>
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
          <button className="btn primary w100" style={{ marginTop: 14 }} disabled={!can.broadcast}
            onClick={() => toast('Broadcast queued to ' + (cat === 'marketing' ? optedIn : linked).toLocaleString() + ' recipients (audit logged)')}>
            <Icon name="megaphone" size={15} /> Queue broadcast
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
  const [global, setGlobal] = useState(true);
  const intents = DB.CONVOS.flatMap((c) => c.msgs.filter((m) => m.intent).map((m) => ({ ...m, user: c.user, msisdn: c.msisdn })));
  return (
    <div>
      <PageHead title="AI controls" sub="The model only proposes — validation, confirm and PIN still gate every movement." />
      <div className="grid-2-1">
        <Card title="Recent parsed intents" sub="Stored on each inbound message" pad={false}>
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
        </Card>
        <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
          <Card title="Global kill switch" sub="SystemSetting · ai_enabled_global">
            <div className="kill">
              <div>
                <b>{global ? 'AI intent layer is ON' : 'AI intent layer is OFF'}</b>
                <p className="dim sm" style={{ margin: '4px 0 0' }}>{global ? 'Free-form text is mapped to intents by Claude (temp 0, tool calling).' : 'Channel is fully menu-driven. Keywords and numbered menus still work.'}</p>
              </div>
              <Toggle on={global} disabled={!can.ai} label="global AI"
                onChange={(v) => { setGlobal(v); toast('ai_enabled_global = ' + v + ' (audit logged)'); }} />
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
        <Card title="System settings" sub="SystemSetting key/value — flippable at runtime" pad={false}>
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
        <Card title="Team" sub="4 members">
          {DB.TEAM.map((m) => (
            <div key={m.email} className="kv tight">
              <span><b>{m.name}</b> <em className="dim sm">{m.email}</em></span>
              <Badge v={m.role === 'super_admin' ? 'success' : m.role === 'finance' ? 'human' : m.role === 'support' ? 'bot' : 'draft'}>{m.role}</Badge>
            </div>
          ))}
          <button className="btn ghost w100" style={{ marginTop: 12 }} disabled={!can.settings} onClick={() => toast('Invite sent (audit logged)')}>Invite member</button>
        </Card>
      </div>
      <Card title="Role permissions" sub="What each role can do" pad={false}>
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
