/**
 * Upload a file to the server for a given conversation.
 * @param {string} convId
 * @param {File} file
 * @returns {Promise<{filename: string, path: string, mime_type: string}>}
 */
export async function uploadFile(convId, file) {
  const form = new FormData();
  form.append('file', file);
  const resp = await fetch(`/api/upload/${encodeURIComponent(convId)}`, {
    method: 'POST',
    body: form,
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error || `Upload failed: ${resp.status}`);
  }
  return resp.json();
}
