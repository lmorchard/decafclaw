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
