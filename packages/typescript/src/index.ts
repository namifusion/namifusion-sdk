export {
  NamiFusionError,
  AuthenticationError,
  InsufficientCreditsError,
  ForbiddenError,
  NotFoundError,
  InvalidRequestError,
  RateLimitError,
  ServerError,
  TaskFailedError,
  errorFromResponse,
} from "./errors.js";
export type { NamiFusionErrorOptions } from "./errors.js";

export type {
  Task,
  RunResult,
  TaskStatus,
  ClientOptions,
  RunOptions,
  SubscribeOptions,
  ListTasksParams,
  ListTasksResult,
} from "./types.js";

export { NamiFusion } from "./client.js";
export { toDataUrl } from "./files.js";

// http.ts's `request()` is an internal transport primitive consumed by
// client.ts — intentionally not part of the public surface.
