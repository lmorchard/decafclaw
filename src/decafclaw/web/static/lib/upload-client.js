import { DefaultService } from './api-client/index.js';

/**
 * Upload a file using the generated multipart contract.
 * @param {Parameters<typeof DefaultService.wrapperApiUploadConvIdPost>[0]} convId
 * @param {Parameters<typeof DefaultService.wrapperApiUploadConvIdPost>[1]['file']} file
 * @returns {ReturnType<typeof DefaultService.wrapperApiUploadConvIdPost>}
 */
export function uploadFile(convId, file) {
  return DefaultService.wrapperApiUploadConvIdPost(convId, { file });
}

/**
 * Upload a file while reporting progress, using XMLHttpRequest because `fetch`
 * cannot observe request-body upload progress.
 *
 * Mirrors the generated contract for the same endpoint: URL encoding,
 * same-origin credentials, the `{filename, path, mime_type}` attachment body,
 * and the established error messages (`{error}` bodies, else
 * `Upload failed: <status>`).
 *
 * @param {string} convId
 * @param {File} file
 * @param {(fraction: number) => void} [onProgress] Receives 0..1 as bytes go out.
 * @returns {Promise<import('./api-client/index.js').AttachmentResponse>}
 */
export function uploadFileWithProgress(convId, file, onProgress = () => {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/upload/${encodeURIComponent(convId)}`);
    xhr.withCredentials = true;

    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && event.total > 0) {
        onProgress(event.loaded / event.total);
      }
    });

    xhr.addEventListener('load', () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        let serverError;
        try {
          const body = JSON.parse(xhr.responseText);
          serverError = body !== null && typeof body === 'object' ? body.error : undefined;
        } catch {
          serverError = undefined;
        }
        reject(new Error(serverError ? String(serverError) : `Upload failed: ${xhr.status}`));
        return;
      }
      try {
        resolve(JSON.parse(xhr.responseText));
      } catch (error) {
        reject(error);
      }
    });
    xhr.addEventListener('error', () => reject(new TypeError('Network request failed')));
    xhr.addEventListener('abort', () => reject(new DOMException('Upload aborted', 'AbortError')));

    const formData = new FormData();
    formData.append('file', file);
    xhr.send(formData);
  });
}
