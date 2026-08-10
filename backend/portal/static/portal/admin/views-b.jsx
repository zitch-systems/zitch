// Zitch Admin — views B: WhatsApp inbox, Broadcasts, AI controls, Audit log, Settings (live)
const { useState: useStateB, useEffect: useEffectB } = React;
const DB = window.ZADM;

// ================= WHATSAPP INBOX =================
function WaInbox({ toast, refresh }) {
  const { can } = useRole();
  const [convos, setConvos] = useStateB(DB.CONVOS);
  const [selIdx, setSelIdx] = useStateB(0);
  const [msgs, setMsgs] = useStateB([]);
  const [reply, setReply] = useStateB('');
  // Clamp: a reload can shrink the list below the selected index (the inbox is
  // the top-50 by activity), which would render `c.user` of undefined — a
  // white-screen crash of the whole portal.
  const c = convos[Math.min(selIdx, convos.length - 1)];

  const loadThread = (msisdn) =>
    ZAPI.thread(msisdn).then((r) => setMsgs(r.msgs)).catch((e) => toast('⚠ ' + e.message));
  const reload = () =>
    ZAPI.load.inbox().then(() => { setConvos(DB.CONVOS); if (c) loadThread(c.msisdn); }).catch(() => {});
  useEffectB(() => { if (c) loadThread(c.msisdn); }, [selIdx, convos.length]);

  const act = async (fn, msg) => {
    try { await fn(); toast(msg); reload(); } catch (e) { toast('⚠ ' + e.message); }
  };
  const sendReply = () => {
    if (!reply.trim()) return;
    const text = reply; setReply('');
    act(() => ZAPI.reply(c.msisdn, text), 'Agent reply sent (audit logged)');
  };

  if (!convos.length) return (<div><PageHead title="WhatsApp" sub="Conversation monitor" /><Card><Empty text="No conversations yet — they appear as soon as a message hits the webhook." /></Card></div>);

  return (
    <div>
      <PageHead title="WhatsApp" sub="Conversation monitor — while a chat is with a human, the bot stays silent." />
      <div className="wa-layout">
        <div className="card convo-list">
          {convos.map((cv, i) => (
            <button key={cv.msisdn} className={'convo' + (i === selIdx ? ' on' : '')} onClick={() => setSelIdx(i)}>
              <span className="avatar">{cv.user === '(unlinked)' ? '?' : cv.user.split(' ').map((w) => w[0]).join('').slice(0, 2)}</span>
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
                onChange={(v) => act(() => ZAPI.convAi(c.msisdn, v), 'Conversation AI ' + (v ? 'enabled' : 'disabled') + ' (audit logged)')} />
              {c.status === 'human'
                ? <button className="btn ghost sm-btn" disabled={!can.wa} onClick={() => act(() => ZAPI.returnBot(c.msisdn), 'Returned to bot (audit logged)')}><Icon name="bot" size={14} /> Return to bot</button>
                : <button className="btn primary sm-btn" disabled={!can.wa} onClick={() => act(() => ZAPI.handover(c.msisdn), 'Bot paused — conversation handed to you (audit logged)')}><Icon name="user" size={14} /> Take over</button>}
            </div>
          </div>
          <div className="chat-scroll">
            {msgs.map((m, i) => (
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
function Broadcasts({ toast, refresh }) {
  const { can } = useRole();
  const [cat, setCat] = useStateB('utility');
  const [tpl, setTpl] = useStateB('');
  const [busy, setBusy] = useStateB(false);
  const { opted_in: optedIn, linked } = DB.BC_META;
  const pending = (DB.APPROVALS || []).filter((r) => r.action === 'whatsapp.broadcast');
  const queue = async () => {
    setBusy(true);
    try {
      const r = await ZAPI.broadcast(tpl.trim(), cat);
      toast('Broadcast approval request #' + r.approval_id + ' created — a different operator must approve it');
      setTpl(''); refresh();
    } catch (e) { toast('⚠ ' + e.message); }
    setBusy(false);
  };
  const decide = async (id, approve) => {
    setBusy(true);
    try {
      await ZAPI.approvalDecide(id, approve, approve ? 'Campaign checked' : 'Campaign rejected');
      toast(approve ? 'Campaign approved and queued for delivery' : 'Campaign rejected');
      await refresh();
    } catch (e) { toast('⚠ ' + e.message); }
    setBusy(false);
  };
  return (
    <div>
      <PageHead title="Broadcasts" sub="Template campaigns over WhatsApp. STOP / UNSUBSCRIBE flips marketing opt-in off automatically." />
      <div className="grid-2-1">
        <Card title="Campaigns" pad={false}>
          {DB.BROADCASTS.length ? (
            <table className="tbl">
              <thead><tr><th>Template</th><th>Category</th><th>Status</th><th className="r">Queued</th><th className="r">Delivered</th><th className="r">Read</th><th className="r">Failed</th><th className="r">Unknown</th></tr></thead>
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
                    <td className="r num">{(b.unknown || 0).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty text="No campaigns yet." />}
        </Card>
        <Card title="New broadcast" sub="Meta-approved templates only">
          <label className="f-label">Template name</label>
          <input className="f-input" value={tpl} onChange={(e) => setTpl(e.target.value)} placeholder="e.g. fx_rate_drop_alert" />
          <label className="f-label">Category</label>
          <div className="chips" style={{ marginBottom: 4 }}>
            {['utility', 'marketing'].map((x) => <button key={x} className={'chip' + (cat === x ? ' on' : '')} onClick={() => setCat(x)}>{x}</button>)}
          </div>
          <div className={'note ' + (cat === 'marketing' ? 'warn' : '')}>
            {cat === 'marketing'
              ? <React.Fragment><Icon name="alert" size={14} /> Marketing reaches only the <b>{optedIn.toLocaleString()}</b> opted-in users.</React.Fragment>
              : <React.Fragment><Icon name="check" size={14} /> Utility reaches all <b>{linked.toLocaleString()}</b> linked numbers.</React.Fragment>}
          </div>
          <button className="btn primary w100" style={{ marginTop: 14 }} disabled={!can.broadcast || !tpl.trim() || busy} onClick={queue}>
            <Icon name="megaphone" size={15} /> {busy ? 'Sending…' : 'Queue broadcast'}
          </button>
          {!can.broadcast && <p className="rbac-note"><Icon name="lock" size={13} /> Support or super admin only.</p>}
        </Card>
      </div>
      <div style={{ marginTop: 16 }}>
        <Card title="Pending approvals" sub="A campaign can only be approved by a different broadcast operator" pad={false}>
          {pending.length ? (
            <table className="tbl">
              <thead><tr><th>Request</th><th>Template</th><th>Category</th><th>Requested by</th><th></th></tr></thead>
              <tbody>{pending.map((r) => (
                <tr key={r.id}>
                  <td className="mono">#{r.id}</td>
                  <td className="mono">{r.payload.template_name}</td>
                  <td>{r.payload.category}</td>
                  <td>{r.requested_by}{r.is_own_request ? ' (you)' : ''}</td>
                  <td className="r">
                    <button className="btn ghost sm-btn" disabled={busy || !r.can_decide} onClick={() => decide(r.id, false)}>Reject</button>{' '}
                    <button className="btn primary sm-btn" disabled={busy || !r.can_decide} onClick={() => decide(r.id, true)}>Approve</button>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          ) : <Empty text="No campaigns are waiting for your role." />}
        </Card>
      </div>
    </div>
  );
}

// ================= AI CONTROLS =================
function AiControls({ toast, refresh }) {
  const { can } = useRole();
  const toggle = async (v) => {
    try { await ZAPI.aiGlobal(v); toast('ai_enabled_global = ' + v + ' (audit logged)'); refresh(); }
    catch (e) { toast('⚠ ' + e.message); }
  };
  const on = DB.AI.enabled;
  const configured = (DB.AI_CFG || {}).configured;
  // Four scopes gate the AI and only two are knowable from here, so the page
  // says which of those two is blocking rather than leaving an operator to
  // guess why a switch reading ON produces no intents.
  const blocker = !configured
    ? { text: 'No model key saved — nothing can parse a sentence yet. Add one under Model provider.', tone: 'warn' }
    : !on
      ? { text: 'A key is installed but the kill switch is off, so every message goes down the deterministic path.', tone: 'warn' }
      : (DB.AI.consented === 0 && DB.AI.linked > 0)
        ? { text: 'Key installed and the switch is on, but none of your ' + DB.AI.linked + ' linked customers have opted in yet — free-form text goes to a third-party model, so each customer grants that themselves by replying “ai on” in the chat.', tone: 'warn' }
        : { text: 'Key installed, switch on, ' + (DB.AI.consented || 0) + ' of ' + (DB.AI.linked || 0) + ' linked customers opted in (they reply “ai on”). Handover disables it per conversation.', tone: 'ok' };
  return (
    <div>
      <PageHead title="AI controls" sub="The model only proposes — validation, confirm and PIN still gate every movement." />
      <div className={'ai-status ' + blocker.tone} style={{ marginBottom: 14 }}>
        <Icon name={blocker.tone === 'ok' ? 'check' : 'lock'} size={14} /> <span>{blocker.text}</span>
      </div>
      <div className="grid-2-1">
        <Card title="Recent parsed intents" sub="Stored on each inbound message" pad={false}>
          {DB.AI.intents.length ? (
            <table className="tbl">
              <thead><tr><th>Number</th><th>Message</th><th>Parsed intent</th><th className="r">When</th></tr></thead>
              <tbody>
                {DB.AI.intents.map((m, i) => (
                  <tr key={i}>
                    <td className="mono sm">{m.msisdn}</td>
                    <td className="dim">“{m.text}”</td>
                    <td><span className="mono sm">{(m.intent && (m.intent.name || m.intent.intent)) || '—'}</span></td>
                    <td className="r dim num">{DB.fmtT(m.t)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty text="No parsed intents yet — they appear when the AI layer routes a free-form message." />}
        </Card>
        <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
          <Card title="Global kill switch" sub="SystemSetting · ai_enabled_global">
            <div className="kill">
              <div>
                <b>{on ? 'AI intent layer is ON' : 'AI intent layer is OFF'}</b>
                <p className="dim sm" style={{ margin: '4px 0 0' }}>{on ? 'Free-form text is mapped to intents by Claude (temp 0, tool calling).' : 'Channel is fully menu-driven. Keywords and numbered menus still work.'}</p>
              </div>
              <Toggle on={on} disabled={!can.ai} label="global AI" onChange={toggle} />
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
          <ModelProviderCard toast={toast} refresh={refresh} />
        </div>
      </div>
    </div>
  );
}

// ---- model provider: which LLM reads the customer's sentence -------------- //
function ModelProviderCard({ toast, refresh }) {
  const { can } = useRole();
  const cfg = DB.AI_CFG || {};
  const [provider, setProvider] = useStateB(cfg.provider || 'anthropic');
  const [model, setModel] = useStateB(cfg.model || '');
  const [baseUrl, setBaseUrl] = useStateB(cfg.base_url || '');
  const [apiKey, setApiKey] = useStateB('');
  const [busy, setBusy] = useStateB(false);

  if (cfg.denied) {
    return (
      <Card title="Model provider">
        <p className="rbac-note"><Icon name="lock" size={13} /> Super admin only.</p>
      </Card>
    );
  }
  const spec = (cfg.providers || []).find((p) => p.id === provider) || {};
  const pick = (id) => {
    setProvider(id);
    const s = (cfg.providers || []).find((p) => p.id === id) || {};
    // Follow the provider's own default rather than carrying the previous
    // provider's model name across, which would never be a valid id.
    setModel(s.default_model || '');
    setBaseUrl(s.base_url || '');
  };
  const save = async () => {
    setBusy(true);
    try {
      // api_key is sent only when the operator typed a new one — the server
      // keeps the existing key otherwise, so editing a model never wipes it.
      const body = { provider, model, base_url: baseUrl };
      if (apiKey) body.api_key = apiKey;
      await ZAPI.aiConfigSave(body);
      setApiKey('');
      toast('Model provider saved (audit logged)');
      refresh();
    } catch (e) { toast('⚠ ' + e.message); }
    setBusy(false);
  };
  const test = async () => {
    setBusy(true);
    try { const r = await ZAPI.aiTest(); toast((r.success ? '✅ ' : '⚠ ') + r.message); }
    catch (e) { toast('⚠ ' + e.message); }
    setBusy(false);
  };

  return (
    <Card title="Model provider" sub="Which LLM reads the customer's sentence. Swapping it cannot change what the platform will do — the router still validates every intent.">
      <div className="form-rows">
        <label className="fr">
          <span>Provider</span>
          <select value={provider} disabled={!can.settings} onChange={(e) => pick(e.target.value)}>
            {(cfg.providers || []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </label>
        <label className="fr">
          <span>Model</span>
          <input value={model} disabled={!can.settings} placeholder={spec.default_model || 'model id'}
                 onChange={(e) => setModel(e.target.value)} />
        </label>
        {spec.needs_base_url && (
          <label className="fr">
            <span>Base URL</span>
            <input value={baseUrl} disabled={!can.settings} placeholder="https://…/v1"
                   onChange={(e) => setBaseUrl(e.target.value)} />
          </label>
        )}
        <label className="fr">
          <span>API key</span>
          <input type="password" value={apiKey} disabled={!can.settings} autoComplete="new-password"
                 placeholder={cfg.api_key_masked ? `${cfg.api_key_masked} — leave blank to keep` : 'paste the provider key'}
                 onChange={(e) => setApiKey(e.target.value)} />
        </label>
      </div>
      <p className="dim sm" style={{ margin: '10px 0 0' }}>
        Stored encrypted; never shown again after saving.
        {spec.key_url ? <> <a href={spec.key_url} target="_blank" rel="noreferrer noopener">Get a key ↗</a></> : null}
      </p>
      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <button className="btn" disabled={!can.settings || busy} onClick={save}>Save</button>
        <button className="btn ghost" disabled={!can.settings || busy} onClick={test}>Test connection</button>
      </div>
      {!cfg.configured && <p className="rbac-note" style={{ marginTop: 10 }}>
        <Icon name="lock" size={13} /> Not configured — the channel is fully deterministic until a key is saved.
      </p>}
      {!can.settings && <p className="rbac-note"><Icon name="lock" size={13} /> Super admin only.</p>}
    </Card>
  );
}

// ================= AUDIT LOG =================
function Audit({ toast }) {
  const [q, setQ] = useStateB('');
  const [rows, setRows] = useStateB(DB.AUDIT);
  const refetch = (nq) => ZAPI.load.audit(nq).then(() => setRows(DB.AUDIT)).catch((e) => toast('⚠ ' + e.message));
  return (
    <div>
      <PageHead title="Audit log" sub="Append-only. Every admin action and sensitive event, with before/after." right={<SearchBox value={q} onChange={(v) => { setQ(v); refetch(v); }} placeholder="Search actor, action…" />} />
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
                  <td><b className="num">{s.value || '—'}</b></td>
                  <td className="dim sm">{s.desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Team" sub={DB.TEAM.length + ' member(s)'}>
          {DB.TEAM.map((m) => (
            <div key={m.email} className="kv tight">
              <span><b>{m.name}</b> <em className="dim sm">{m.email}</em></span>
              <Badge v={m.role === 'super_admin' ? 'success' : m.role === 'finance' ? 'human' : m.role === 'support' ? 'bot' : 'draft'}>{m.role}</Badge>
            </div>
          ))}
          <div className="note" style={{ marginTop: 12 }}><Icon name="lock" size={14} /> Staff accounts and role groups are managed in Django admin.</div>
        </Card>
      </div>
      <DjangoAdminCard />
      <Card title="Role permissions" sub="Enforced server-side on every endpoint" pad={false}>
        <table className="tbl">
          <thead><tr><th>Permission</th>{DB.ROLES.map((r) => <th key={r} className="r">{r}</th>)}</tr></thead>
          <tbody>
            {DB.PERMS.map((p) => (
              <tr key={p.perm}>
                <td>{p.perm}</td>
                {DB.ROLES.map((r) => (
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

// ---- Django admin: the escape hatch for the long tail ------------------- //
function DjangoAdminCard() {
  const d = DB.DJANGO_ADMIN || {};
  return (
    <Card title="Django admin"
          sub="The portal owns the daily workflows; this is the full model surface for everything else. Opens in a new tab.">
      {d.available ? (
        <div className="admin-links">
          {(d.sections || []).map((sec) => (
            <a key={sec.url} href={sec.url} target="_blank" rel="noreferrer noopener">{sec.label} ↗</a>
          ))}
        </div>
      ) : (
        <p className="rbac-note"><Icon name="lock" size={13} /> {d.message || 'Restricted to superusers.'}</p>
      )}
    </Card>
  );
}

Object.assign(window, { WaInbox, Broadcasts, AiControls, Audit, Settings });
