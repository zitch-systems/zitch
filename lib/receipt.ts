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

/** Badge colours per outcome — a shared document must never dress a pending or
 *  failed transaction in success green. */
const badgeTone = (status: string): { bg: string; fg: string } => {
  if (/fail|revers|declin/i.test(status)) return { bg: '#fdecec', fg: '#c0392b' };
  if (/pend|process/i.test(status)) return { bg: '#fdf3e0', fg: '#b9770e' };
  return { bg: '#e8f6ee', fg: '#128c4a' };
};

/**
 * The PDF body. Deliberately self-contained — no remote fonts or images, because
 * the renderer may run with no network and a receipt that half-loads is worse
 * than a plain one.
 *
 * `status` is stamped on the badge. It defaults to Successful because most
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
}): string => `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  /* Sizes are in CSS px, which the print renderer maps at 96dpi — so 13px was
     under 10pt, below what most people can read on paper without holding it up
     to the light, and well under the ~11pt a printed document normally sets body
     text at. Everything here is scaled to read comfortably at A4, with the row
     text at 15px (~11.3pt) and the amount large enough to find at a glance. */
  @page { margin: 0; }
  body { margin: 0; font-family: -apple-system, "Helvetica Neue", Roboto, sans-serif;
         color: #11181c; -webkit-font-smoothing: antialiased; }
  .band { background: #0FA295; color: #fff; padding: 34px 44px 26px; }
  .mark { font-size: 36px; font-weight: 800; letter-spacing: -.5px; }
  .kicker { font-size: 13px; opacity: .9; margin-top: 6px; letter-spacing: 1px; text-transform: uppercase; }
  .body { padding: 36px 44px 0; }
  h1 { font-size: 27px; margin: 0; line-height: 1.25; }
  .msg { color: #5c686e; font-size: 15px; margin: 10px 0 28px; line-height: 1.55; }
  table { width: 100%; border-collapse: collapse; }
  td { padding: 15px 0; border-bottom: 1px solid #e6eaec; font-size: 15px;
       line-height: 1.45; vertical-align: top; }
  td.k { color: #5c686e; padding-right: 18px; }
  /* The value column wraps rather than running under the label: an account name
     long enough to collide is exactly the kind a receipt must still show whole. */
  td.v { text-align: right; font-weight: 600; word-break: break-word; }
  tr.total td { font-size: 21px; font-weight: 800; border-bottom: 0; padding-top: 20px; }
  .foot { padding: 26px 44px; color: #7c878c; font-size: 13px; line-height: 1.5; }
  .badge { display: inline-block; background: ${badgeTone(status).bg}; color: ${badgeTone(status).fg};
           border-radius: 20px; padding: 7px 16px; font-size: 14px; font-weight: 700; float: right; }
  /* Tiled diagonal watermark. position:fixed so it repeats on every printed page
     rather than only the first, and it must not add to the flow — a watermark
     that pushed the rows down would change the document it is marking. It sits
     ABOVE the content (pointer-events are irrelevant in print) so a crop cannot
     lift a clean region out of the middle, and is faint enough not to fight the
     amount. */
  .wm { position: fixed; inset: -30%; z-index: 2; transform: rotate(-30deg);
        opacity: .05; pointer-events: none; overflow: hidden; }
  .wm div { font-size: 22px; font-weight: 800; letter-spacing: 2px; color: #0FA295;
            white-space: nowrap; line-height: 2.6; }
  .wm div:nth-child(even) { text-indent: 46px; }
</style></head><body>
<div class="wm">${Array.from({ length: 26 }, (_, i) =>
  `<div>${'ZITCH &nbsp;&bull;&nbsp; '.repeat(14)}</div>`).join('')}</div>
<div class="band"><div class="mark">zitch</div><div class="kicker">Transaction receipt</div></div>
<div class="body">
  <span class="badge">${esc(status)}</span>
  <h1>${esc(title)}</h1>
  <p class="msg">${esc(message)}</p>
  <table>${rows
    .map(([k, v, strong]) =>
      `<tr${strong ? ' class="total"' : ''}><td class="k">${esc(k)}</td><td class="v">${esc(v)}</td></tr>`)
    .join('')}</table>
</div>
<div class="foot">Generated by Zitch · zitch.ng — keep this receipt for your records.</div>
</body></html>`;

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
  const FS = await import('expo-file-system/legacy');
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
  } catch {
    return 'failed';
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
  if (Platform.OS === 'web') return 'unsupported';
  try {
    const { uri, mime, filename } = await exportReceipt(format, src);

    if (format === 'jpeg') {
      const MediaLibrary = await import('expo-media-library');
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
      const FS = await import('expo-file-system/legacy');
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
  } catch {
    return 'failed';
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
    case 'failed': return 'Could not create the receipt file';
    case 'cancelled': return null;   // the user backed out; say nothing
  }
};
