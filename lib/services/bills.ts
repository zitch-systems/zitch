// The one remaining vertical that uses apiJson: the FX rate. Betting top-ups and
// exam pins were bought through the 9PSB VAS rail and went with it. The
// airtime/data/cable/electricity flows branch on response.ok via apiPost and are
// intentionally left on their screens.
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import type { ApiResult } from '@/lib/services/types';

type BillResult = ApiResult<Record<string, any>>;

export const convertService = {
  getRate: () => apiJson<BillResult>(EP.convert.fx),
};
