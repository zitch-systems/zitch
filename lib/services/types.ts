// Shared response contracts for the service layer.
//
// Every backend response that `apiJson` returns is reduced to a uniform
// `{ success, message, ... }`-style object (a non-JSON/offline/timeout response
// degrades to `{ success:false, message }`). Services extend this base with the
// fields a given endpoint adds, so call sites get typed data instead of `any`.

export type ApiResult<T = {}> = {
  success?: boolean;
  status?: boolean; // some legacy endpoints use `status` instead of `success`
  message?: string;
} & T;
