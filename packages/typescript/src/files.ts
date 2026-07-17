/**
 * File helpers for the NamiFusion SDK. The API has no public upload
 * endpoint — file inputs go through a model's `auto_upload_base64`
 * parameter as a base64 string or data URL — so `toDataUrl` is the
 * primary way callers turn binary data into something they can put in
 * `input`.
 */

/** Encodes binary data as a `data:` URL (`data:{mimeType};base64,...`). */
export async function toDataUrl(
  data: Uint8Array | ArrayBuffer | Blob,
  mimeType: string,
): Promise<string> {
  const bytes = await toUint8Array(data);
  return `data:${mimeType};base64,${uint8ArrayToBase64(bytes)}`;
}

async function toUint8Array(data: Uint8Array | ArrayBuffer | Blob): Promise<Uint8Array> {
  if (data instanceof Uint8Array) return data;
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  // Blob (browser File/Blob, or Node's global Blob).
  return new Uint8Array(await data.arrayBuffer());
}

/**
 * Base64-encodes bytes. Prefers Node's `Buffer` when present; falls back
 * to `btoa` over a chunked binary string for browsers (chunked to avoid
 * blowing the call stack via `String.fromCharCode(...bytes)` spread on
 * large inputs).
 */
function uint8ArrayToBase64(bytes: Uint8Array): string {
  if (typeof Buffer !== "undefined") {
    return Buffer.from(bytes).toString("base64");
  }

  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}
