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
} from "./types.js";

// http.ts's `request()` is an internal transport primitive consumed by
// client.ts (Task 3) — intentionally not part of the public surface.
