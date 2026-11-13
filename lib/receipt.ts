/**
 * Turning a receipt on screen into a file the user actually keeps.
 *
 * "Save" used to copy plain text to the clipboard. That is a poor artifact: it
 * survives one paste and nothing about it says Zitch. What people do with a
 * transfer receipt is send it to whoever they paid, so it has to be a real file —
 * an image to drop into a chat, or a PDF to attach to an email or file with an
 * accountant. Both, because which one is right depends entirely on where it's going.
 *
 * The two formats come from two different sources on purpose:
 *   * JPEG is a screenshot of the receipt card the user is looking at, so what
 *     they share is exactly what they saw — same fonts, same theme, no second
 *     rendering that could drift out of sync with the screen.
 *   * PDF is rendered from HTML, because a page-shaped document should be laid
 *     out for paper, not scaled up from a phone-width bitmap.
 *
 * The pure parts (stamp, filename, HTML) are separated from the native calls so
 * they can be tested without a device.
 */
import { Platform } from 'react-native';
import * as FileSystem from 'expo-file-system/legacy';
import * as MediaLibrary from 'expo-media-library';
import qrcode from 'qrcode-generator';
import { BANK_WHATSAPP, BANK_WHATSAPP_DISPLAY, SUPPORT_EMAIL } from '@/components/configFiles/links';
import { MARK_TEAL_PNG, MARK_WHITE_PNG } from '@/lib/receiptArt';
import { transactionStatusPresentation, type TxnState } from '@/lib/transactionStatus';
import { WHATSAPP_GLYPH_PATH } from '@/lib/whatsappGlyph';

export type ReceiptRow = [string, string, boolean?];
export type ReceiptFormat = 'jpeg' | 'pdf';

/** What happened, in the user's terms — the caller turns this into one line of copy. */
export type ExportOutcome =
  | 'saved'        // written somewhere the user can find it again
  | 'shared'       // handed to the OS share sheet
  | 'cancelled'    // user backed out of the system dialog
  | 'denied'       // permission refused
  | 'unsupported'  // platform can't do it (web)
  | 'failed';

/**
 * Why the last export failed, for the message the user reads.
 *
 * Every path here used to end in a bare `catch { return 'failed' }`, so a
 * customer looking at "Could not create the receipt file" and an engineer
 * reading the code had exactly the same information: none. The receipt is the
 * only evidence of a payment someone still has, so a failure to produce one
 * needs to say which step broke — capturing the card, rendering the PDF, or
 * writing the file.
 *
 * Kept module-level and deliberately short-lived: it is read immediately by the
 * caller building the message, and it holds no transaction data, only the name
 * of the step and the platform error's own text.
 */
let lastFailure = '';

export const lastExportFailure = (): string => lastFailure;

const noteFailure = (step: string, error: unknown): 'failed' => {
  const detail = error instanceof Error ? error.message : String(error ?? '');
  lastFailure = detail ? `${step}: ${detail}` : step;
  // Reaches the device log / crash reporter, where the message on screen cannot.
  console.warn('[receipt] export failed —', lastFailure);
  return 'failed';
};

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/**
 * Date and time as the receipt shows them. Formatted by hand rather than through
 * `toLocaleString`, whose output depends on which Intl data the JS engine shipped
 * with — a receipt is a record, so the same transaction must not read differently
 * on two phones.
 */
export const receiptStamp = (when: Date = new Date()): { date: string; time: string } => {
  const pad = (n: number) => String(n).padStart(2, '0');
  const h = when.getHours();
  const hour12 = h % 12 === 0 ? 12 : h % 12;
  return {
    date: `${pad(when.getDate())} ${MONTHS[when.getMonth()]} ${when.getFullYear()}`,
    time: `${pad(hour12)}:${pad(when.getMinutes())} ${h < 12 ? 'AM' : 'PM'}`,
  };
};

/**
 * A filename that survives every filesystem: the reference reduced to safe
 * characters, never empty, never unbounded. The name is what the recipient sees
 * in their downloads folder, so it leads with the brand.
 */
export const receiptFileName = (reference: string, format: ReceiptFormat): string => {
  const slug = (reference || '').replace(/[^A-Za-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48);
  return `Zitch-Receipt${slug ? `-${slug}` : ''}.${format === 'pdf' ? 'pdf' : 'jpg'}`;
};

/**
 * Who paid. A receipt that names only the recipient answers half the question a
 * receipt exists to answer — the person receiving it needs to see which account
 * the money left, and so does anyone the file is forwarded to afterwards.
 */
export type ReceiptSender = {
  name?: string;
  account?: string;
  bank?: string;
};

/**
 * Sender lines, omitting whatever we don't actually know. A receipt with a blank
 * "Sender: —" row is worse than one without the row: it reads as missing data on
 * a document people treat as proof, when the truth is simply that this receipt
 * had nothing to put there.
 */
export const senderRows = (sender?: ReceiptSender): ReceiptRow[] => {
  const rows: ReceiptRow[] = [];
  if (!sender) return rows;
  if (sender.name?.trim()) rows.push(['Sender', sender.name.trim()]);
  if (sender.account?.trim()) rows.push(['Sender account', sender.account.trim()]);
  if (sender.bank?.trim()) rows.push(['Sender bank', sender.bank.trim()]);
  return rows;
};

const esc = (s: string) =>
  String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/**
 * An account number the way the bank's own alerts print it: 0123****89. The
 * receipt is forwarded, and a reader recognises the account from the ends.
 */
export const maskAccount = (number: string): string => {
  const digits = String(number || '').replace(/\D/g, '');
  if (digits.length < 6) return digits;
  return `${digits.slice(0, 4)}****${digits.slice(-2)}`;
};

export type ReceiptParty = { label: string; name: string; detail: string };

/** What goes where on the receipt. */
export type ReceiptLayout = {
  title: string;
  /** The headline: the money that moved. Empty when the receipt has none. */
  amount: string;
  status: string;
  when: string;
  parties: ReceiptParty[];
  /** A token or PIN: the one value someone types somewhere else. */
  highlights: [string, string][];
  rows: ReceiptRow[];
};

// Labels that name the money that moved, in the order they are preferred.
const HEADLINE = /^(transfer amount|amount|loan amount|requested loan amount|principal|requested principal)$/i;
const HIGHLIGHT = /^(token|pins?|pin \d+)$/i;

/**
 * Arrange the rows a screen passed into the receipt's layout. This mirrors the
 * WhatsApp receipt (backend whatsapp/receipt.py `receipt_layout`), so a
 * customer meets the same document wherever they paid.
 *
 * - The amount is the headline and is not repeated as a row. A "Total" or
 *   "Amount" row repeating it is dropped: printed twice it reads as a fault.
 * - Date and time sit under the status, not in the rows.
 * - The recipient's name, bank and account read as one entry, and so do the
 *   sender's. The sender's account is masked; the receipt gets forwarded.
 * - A token or PIN gets a box of its own.
 * - Everything else stays an ordinary row, in the caller's order, with the
 *   reference last.
 *
 * Nothing here states an outcome: the status pill alone does, from `status`.
 */
export const receiptLayout = ({ title, rows, status = 'Successful' }: {
  title: string;
  rows: ReceiptRow[];
  status?: string;
}): ReceiptLayout => {
  const pairs: ReceiptRow[] = rows.map(([k, v, strong]) => [String(k).trim(), String(v ?? '').trim(), !!strong]);
  const take = (test: (row: ReceiptRow) => boolean): ReceiptRow | undefined => {
    const index = pairs.findIndex(test);
    return index < 0 ? undefined : pairs.splice(index, 1)[0];
  };
  const named = (pattern: RegExp) => take(([k]) => pattern.test(k));

  const headline = named(HEADLINE) ?? take(([, , strong]) => !!strong);
  const amount = headline?.[1] ?? '';
  if (amount) {
    for (let i = pairs.length - 1; i >= 0; i -= 1) {
      if (/^(total|amount)$/i.test(pairs[i][0]) && pairs[i][1] === amount) pairs.splice(i, 1);
    }
  }
  const when = [named(/^date$/i)?.[1], named(/^time$/i)?.[1]].filter(Boolean).join(', ');

  const parties: ReceiptParty[] = [];
  const recipient = named(/^(recipient|to|beneficiary)$/i);
  if (recipient) {
    const bank = named(/^bank$/i)?.[1];
    const account = named(/^account$/i)?.[1];
    parties.push({ label: 'Recipient', name: recipient[1], detail: [bank, account].filter(Boolean).join(' · ') });
  }
  const sender = named(/^(sender|from)$/i);
  if (sender) {
    const bank = named(/^sender bank$/i)?.[1];
    const account = named(/^sender account$/i)?.[1];
    parties.push({
      label: 'Sender',
      name: sender[1],
      detail: [bank, account ? maskAccount(account) : ''].filter(Boolean).join(' · '),
    });
  }

  const reference = named(/^reference$/i);
  const highlights = pairs.filter(([k]) => HIGHLIGHT.test(k)).map(([k, v]) => [k, v] as [string, string]);
  const rest = pairs.filter(([k]) => !HIGHLIGHT.test(k));
  if (reference) rest.push(['Reference', reference[1], false]);
  const label = String(status ?? '').trim();
  return {
    title: title.trim() || 'Transaction receipt',
    amount,
    status: label,
    when,
    parties,
    highlights,
    rows: rest,
  };
};

/** The link the receipt's code opens: a chat with the Zitch banking number with
 *  "Hi" typed, which the bot answers with its welcome. */
export const WHATSAPP_START_URL = `https://wa.me/${BANK_WHATSAPP}?text=Hi`;

/**
 * The QR code for `text` as one SVG path on a grid of `size` modules, with no
 * quiet zone (the caller leaves white space around it). Medium error
 * correction: the code is plain and must survive a JPEG and a photo of a screen.
 * Runs of dark modules are merged, so the path stays short.
 */
export const qrPath = (text: string): { size: number; d: string } => {
  const code = qrcode(0, 'M');
  code.addData(text);
  code.make();
  const size = code.getModuleCount();
  let d = '';
  for (let row = 0; row < size; row += 1) {
    let col = 0;
    while (col < size) {
      if (!code.isDark(row, col)) { col += 1; continue; }
      const start = col;
      while (col < size && code.isDark(row, col)) col += 1;
      d += `M${start} ${row}h${col - start}v1h-${col - start}z`;
    }
  }
  return { size, d };
};

/**
 * The receipt's colours. A receipt is a document that gets shared, so it keeps
 * the light theme in every app theme: a forwarded dark-mode screenshot reads as
 * a different company's paper. Mirrors the WhatsApp receipt.
 */
export const RECEIPT_COLORS = {
  paper: '#F4F9F8',
  card: '#FFFFFF',
  ink: '#06231F',
  muted: '#526863',
  line: '#E2EEEB',
  tear: '#CDE0DC',
  brand: '#0FA295',
  brandText: '#066E66',
  brandDeep: '#0C5249',
  brandTint: '#E6F7F4',
  whatsapp: '#25D366',
  whatsappText: '#128C7E',
};

/** Pill colours per outcome — a shared document must never dress a pending or
 *  failed transaction in success green. `mark` is the solid disc behind the icon. */
export const statusTone = (state: TxnState): { bg: string; fg: string; mark: string } => {
  if (state === 'failed') return { bg: '#fdecec', fg: '#c0392b', mark: '#c82e26' };
  if (state === 'pending') return { bg: '#fdf3e0', fg: '#b9770e', mark: '#d08a12' };
  return { bg: '#e8f6ee', fg: '#128c4a', mark: '#00B51D' };
};

const badgeTone = (status: string) => statusTone(transactionStatusPresentation(status).state);

/**
 * The PDF body: the same document as the on-screen receipt and the WhatsApp
 * image, laid out for paper. Deliberately self-contained — no remote fonts or
 * images, because the renderer may run with no network and a receipt that
 * half-loads is worse than a plain one. The mark, the WhatsApp glyph and the QR
 * code are all inline.
 *
 * `status` is stamped on the pill. It defaults to Successful because most
 * receipts are, but callers exporting a pending or failed transaction MUST pass
 * the real status — this file is the artifact a recipient treats as proof.
 */
export const receiptHtml = ({
  title,
  message,
  rows,
  status = 'Successful',
}: {
  title: string;
  message: string;
  rows: ReceiptRow[];
  status?: string;
}): string => {
  const C = RECEIPT_COLORS;
  const layout = receiptLayout({ title, rows, status });
  const tone = badgeTone(status);
  const qr = qrPath(WHATSAPP_START_URL);
  const tr = (k: string, value: string, cls = '') =>
    `<tr${cls ? ` class="${cls}"` : ''}><td class="k">${esc(k)}</td><td class="v">${value}</td></tr>`;
  const body = [
    ...layout.highlights.map(([k, v]) =>
      `<tr class="box"><td colspan="2"><div class="box-k">${esc(k)}</div><div class="box-v">${esc(v)}</div></td></tr>`),
    ...layout.parties.map((p) =>
      tr(p.label, `<div>${esc(p.name)}</div>${p.detail ? `<div class="sub">${esc(p.detail)}</div>` : ''}`)),
    ...layout.rows.map(([k, v, strong]) => tr(k, esc(v), strong ? 'strong' : '')),
  ].join('');
  return `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  /* Sizes are in CSS px, which the print renderer maps at 96dpi: row text at
     15px (~11.3pt) reads comfortably on A4, and nothing is set below 12px. */
  @page { margin: 0; }
  body { margin: 0; font-family: -apple-system, "Helvetica Neue", Roboto, sans-serif;
         color: ${C.ink}; background: ${C.paper}; -webkit-font-smoothing: antialiased; }
  .band { background: linear-gradient(135deg, ${C.brand}, ${C.brandDeep}); color: #fff;
          padding: 30px 44px 120px; display: flex; align-items: center; justify-content: space-between; }
  .brand { display: flex; align-items: center; gap: 12px; }
  .brand img { height: 40px; }
  .mark { font-size: 34px; font-weight: 800; letter-spacing: -.5px; }
  .kind { font-size: 15px; font-weight: 700; color: ${C.brandTint}; }
  .ticket { margin: -96px 34px 0; background: ${C.card}; border-radius: 22px; padding: 30px 34px 18px;
            box-shadow: 0 10px 30px rgba(12, 82, 73, .12); position: relative; }
  .hero { text-align: center; }
  .amount { font-size: 44px; font-weight: 800; letter-spacing: -.5px; line-height: 1.15; }
  .badge { display: inline-block; background: ${tone.bg}; color: ${tone.fg}; border-radius: 20px;
           padding: 7px 16px; font-size: 14px; font-weight: 700; margin-top: 14px; }
  .when { color: ${C.muted}; font-size: 15px; margin-top: 12px; }
  .msg { color: ${C.muted}; font-size: 13px; line-height: 1.5; margin: 8px auto 0; max-width: 460px; }
  .tear { border-top: 2px dashed ${C.tear}; margin: 24px -10px 4px; }
  table { width: 100%; border-collapse: collapse; }
  td { padding: 15px 0; border-bottom: 1px solid ${C.line}; font-size: 15px;
       line-height: 1.45; vertical-align: top; }
  tr:last-child td { border-bottom: 0; }
  td.k { color: ${C.muted}; padding-right: 18px; white-space: nowrap; }
  /* The value column wraps rather than running under the label: an account name
     long enough to collide is exactly the kind a receipt must still show whole. */
  td.v { text-align: right; font-weight: 700; word-break: break-word; }
  .sub { color: ${C.muted}; font-weight: 500; font-size: 13px; margin-top: 3px; }
  tr.strong td.v { font-size: 18px; font-weight: 800; }
  tr.box td { border-bottom: 0; padding: 10px 0 6px; }
  .box-k { background: ${C.brandTint}; color: ${C.brandText}; font-size: 12px; font-weight: 800;
           letter-spacing: 1px; text-transform: uppercase; padding: 14px 18px 0; border-radius: 16px 16px 0 0; }
  .box-v { background: ${C.brandTint}; font-size: 22px; font-weight: 800; letter-spacing: 1px;
           padding: 6px 18px 16px; border-radius: 0 0 16px 16px; word-break: break-word; }
  .wa { margin: 22px 34px 0; padding: 18px 20px; border: 1.5px solid rgba(37, 211, 102, .45);
        border-radius: 20px; display: flex; align-items: center; gap: 16px; position: relative; z-index: 3; }
  .wa-tile { width: 50px; height: 50px; border-radius: 15px; background: ${C.whatsapp};
             display: flex; align-items: center; justify-content: center; flex: none; }
  .wa-text { flex: 1; }
  .wa-kicker { color: ${C.whatsappText}; font-size: 12px; font-weight: 800; letter-spacing: 1px;
               text-transform: uppercase; }
  .wa-title { font-size: 19px; font-weight: 800; margin-top: 4px; }
  .wa-copy { color: ${C.muted}; font-size: 13px; line-height: 1.5; margin-top: 4px; }
  .wa-contact { font-size: 14px; margin-top: 8px; color: ${C.muted}; }
  .wa-contact b { color: ${C.brandText}; }
  .wa-qr { text-align: center; flex: none; }
  .wa-qr svg { background: #fff; padding: 6px; border-radius: 10px; display: block; }
  .wa-qr div { color: ${C.muted}; font-size: 12px; font-weight: 700; margin-top: 6px; }
  .foot { padding: 22px 44px 30px; color: ${C.muted}; font-size: 13px; line-height: 1.6; text-align: center; }
  /* Tiled Z marks over the page. position:fixed so it repeats on every printed
     page; out of flow so it never moves a row; above the content so a crop
     cannot lift a clean region; faint enough not to fight the amount. */
  .wm { position: fixed; inset: 0; z-index: 2; pointer-events: none; opacity: .05;
        background-image: url(${MARK_TEAL_PNG}); background-size: 110px 110px;
        background-repeat: repeat; }
</style></head><body>
<div class="wm"></div>
<div class="band">
  <div class="brand"><img src="${MARK_WHITE_PNG}" alt=""><span class="mark">zitch</span></div>
  <div class="kind">${esc(layout.title)}</div>
</div>
<div class="ticket">
  <div class="hero">
    <div class="amount">${esc(layout.amount || layout.title)}</div>
    <span class="badge">${esc(transactionStatusPresentation(status).label)}</span>
    ${layout.when ? `<div class="when">${esc(layout.when)}</div>` : ''}
    ${message ? `<p class="msg">${esc(message)}</p>` : ''}
  </div>
  <div class="tear"></div>
  <table>${body}</table>
</div>
<div class="wa">
  <div class="wa-tile"><svg width="28" height="28" viewBox="0 0 24 24"><path fill="#fff" d="${WHATSAPP_GLYPH_PATH}"/></svg></div>
  <div class="wa-text">
    <div class="wa-kicker">Zitch WhatsApp Banking</div>
    <div class="wa-title">Bank wherever you chat</div>
    <div class="wa-copy">Send money, buy airtime and check your balance on WhatsApp.</div>
    <div class="wa-contact"><b>zitch.ng</b> &nbsp;·&nbsp; ${esc(BANK_WHATSAPP_DISPLAY)}</div>
  </div>
  <div class="wa-qr">
    <svg width="104" height="104" viewBox="0 0 ${qr.size} ${qr.size}" shape-rendering="crispEdges"><path fill="${C.ink}" d="${qr.d}"/></svg>
    <div>Scan to start</div>
  </div>
</div>
<div class="foot">Need help? ${esc(SUPPORT_EMAIL)}<br>Keep this receipt for your records.</div>
</body></html>`;
};

/** Where the bytes come from. `capture` screenshots the on-screen card (JPEG path). */
export type ReceiptSource = {
  capture: () => Promise<string>;
  html: string;
  reference: string;
};

const withScheme = (uri: string) => (uri.startsWith('file://') || uri.includes('://') ? uri : `file://${uri}`);

/**
 * Produce the file and return where it landed. The name matters: `captureRef` and
 * `printToFileAsync` both hand back a random temp name, and a share sheet shows
 * that name to the recipient, so we always copy to a branded one.
 */
export const exportReceipt = async (
  format: ReceiptFormat,
  src: ReceiptSource,
): Promise<{ uri: string; mime: string; filename: string }> => {
  const FS = FileSystem;
  const filename = receiptFileName(src.reference, format);
  const mime = format === 'pdf' ? 'application/pdf' : 'image/jpeg';

  const raw = format === 'pdf'
    ? (await (await import('expo-print')).printToFileAsync({ html: src.html, base64: false })).uri
    : await src.capture();

  if (!raw) throw new Error('Receipt renderer did not return a file.');
  const exportDirectory = FS.cacheDirectory || FS.documentDirectory;
  // Some native runtimes can temporarily expose neither directory during app
  // restoration. The capture is still a valid file, so share it directly.
  if (!exportDirectory) return { uri: withScheme(raw), mime, filename };
  const target = `${exportDirectory}${filename}`;
  try {
    // A previous export of the same reference would otherwise make copyAsync fail.
    await FS.deleteAsync(target, { idempotent: true });
    await FS.copyAsync({ from: withScheme(raw), to: target });
    return { uri: target, mime, filename };
  } catch {
    // Renaming is a nicety; never lose the receipt over it.
    return { uri: withScheme(raw), mime, filename };
  }
};

/** Hand the file to the OS share sheet (WhatsApp, mail, AirDrop, …). */
export const shareReceipt = async (format: ReceiptFormat, src: ReceiptSource): Promise<ExportOutcome> => {
  lastFailure = '';
  if (Platform.OS === 'web') return 'unsupported';
  try {
    const Sharing = await import('expo-sharing');
    if (!(await Sharing.isAvailableAsync())) return 'unsupported';
    const { uri, mime } = await exportReceipt(format, src);
    await Sharing.shareAsync(uri, {
      mimeType: mime,
      dialogTitle: 'Share receipt',
      UTI: format === 'pdf' ? 'com.adobe.pdf' : 'public.jpeg',
    });
    return 'shared';
  } catch (error) {
    return noteFailure(format === 'pdf' ? 'sharing the PDF' : 'sharing the image', error);
  }
};

/**
 * Put the file somewhere the user can find it later.
 *
 * An image belongs in the photo gallery — that is where people look for a receipt
 * they saved. A PDF has no gallery to go to, so Android writes it to a folder the
 * user picks (normally Downloads) and iOS goes through the system sheet, whose
 * "Save to Files" is the platform's own answer to this. Different mechanics,
 * same promise: the file exists somewhere findable when this returns 'saved'.
 */
export const saveReceipt = async (format: ReceiptFormat, src: ReceiptSource): Promise<ExportOutcome> => {
  lastFailure = '';
  if (Platform.OS === 'web') return 'unsupported';
  try {
    const { uri, mime, filename } = await exportReceipt(format, src);

    if (format === 'jpeg') {
      // SDK 57 throws for the root mutating helper; use the supported legacy
      // namespace for this operation.
      // writeOnly is LOAD-BEARING, not an optimisation. Saving needs no read
      // access to the gallery, so the Android manifest blocks READ_MEDIA_IMAGES
      // (a Play-restricted permission a money app can't justify carrying) — and
      // a read-mode request would therefore fail. Write-only requests nothing at
      // all on Android 13+, WRITE_EXTERNAL_STORAGE on 10–12, add-only on iOS.
      const perm = await MediaLibrary.requestPermissionsAsync(true);
      if (!perm?.granted) return 'denied';
      await MediaLibrary.saveToLibraryAsync(uri);
      return 'saved';
    }

    if (Platform.OS === 'android') {
      const FS = FileSystem;
      const saf = FS.StorageAccessFramework;
      const perm = await saf.requestDirectoryPermissionsAsync();
      if (!perm?.granted) return 'cancelled';
      const base64 = await FS.readAsStringAsync(uri, { encoding: 'base64' });
      const target = await saf.createFileAsync(perm.directoryUri, filename, mime);
      await FS.writeAsStringAsync(target, base64, { encoding: 'base64' });
      return 'saved';
    }

    const Sharing = await import('expo-sharing');
    if (!(await Sharing.isAvailableAsync())) return 'unsupported';
    await Sharing.shareAsync(uri, { mimeType: mime, dialogTitle: 'Save receipt', UTI: 'com.adobe.pdf' });
    return 'shared';
  } catch (error) {
    return noteFailure(format === 'pdf' ? 'saving the PDF' : 'saving the image', error);
  }
};

/** One line of confirmation copy per outcome — the popup never asks a question. */
export const outcomeMessage = (outcome: ExportOutcome, format: ReceiptFormat): string | null => {
  const what = format === 'pdf' ? 'PDF' : 'Image';
  switch (outcome) {
    case 'saved': return `${what} saved to your device`;
    case 'shared': return `${what} ready to share`;
    case 'denied': return 'Zitch needs permission to save to your gallery';
    case 'unsupported': return 'Saving receipts is not available on this device';
    case 'failed': {
      // Naming the step turns an unactionable sentence into a bug report the
      // customer can read out. The reference is still on screen behind this
      // popup, so the payment itself is never in doubt.
      const why = lastExportFailure();
      return why
        ? `Could not create the ${what.toLowerCase()} — ${why}. Check the transaction status and reference on your receipt.`
        : `Could not create the ${what.toLowerCase()} file`;
    }
    case 'cancelled': return null;   // the user backed out; say nothing
  }
};
