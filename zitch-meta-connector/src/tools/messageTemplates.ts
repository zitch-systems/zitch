/**
 * Tool 3: list_whatsapp_message_templates
 *
 * Lists the WABA's approved/pending/rejected message templates — the
 * reusable business-initiated message shapes, not customer conversations.
 * Template *content* (the approved copy) is business-authored marketing/
 * transactional boilerplate, not customer data, so it's safe to return.
 */
import type { Config } from '../config.js';
import { graphGet } from '../metaClient.js';
import type { ListMessageTemplatesInput } from '../schemas.js';

const FIELDS = 'name,status,category,language,quality_score,id,rejected_reason';

interface TemplateNode {
  id?: string;
  name?: string;
  status?: string;
  category?: string;
  language?: string;
  quality_score?: { score?: string; date?: string } | null;
  rejected_reason?: string;
}

interface TemplatesResponse {
  data?: TemplateNode[];
  paging?: { cursors?: { after?: string; before?: string }; next?: string };
}

export interface MessageTemplatesResult {
  wabaId: string;
  templates: Array<{
    id: string | undefined;
    name: string | undefined;
    status: string | undefined;
    category: string | undefined;
    language: string | undefined;
    qualityScore: string | undefined;
    rejectedReason: string | undefined;
  }>;
  nextCursor: string | undefined;
}

export async function listMessageTemplates(
  config: Config,
  input: ListMessageTemplatesInput,
): Promise<MessageTemplatesResult> {
  const wabaId = input.wabaId ?? config.metaWabaId;
  const raw = (await graphGet(config, `${wabaId}/message_templates`, {
    fields: FIELDS,
    limit: input.limit ?? 25,
    after: input.after,
  })) as TemplatesResponse;

  let templates = raw.data ?? [];
  if (input.nameFilter) {
    templates = templates.filter((t) => t.name === input.nameFilter);
  }

  return {
    wabaId,
    templates: templates.map((t) => ({
      id: t.id,
      name: t.name,
      status: t.status,
      category: t.category,
      language: t.language,
      qualityScore: t.quality_score?.score,
      rejectedReason: t.rejected_reason,
    })),
    nextCursor: raw.paging?.cursors?.after,
  };
}
