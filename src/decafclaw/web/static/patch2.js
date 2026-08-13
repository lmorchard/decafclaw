const fs = require('fs');

let code = fs.readFileSync('components/wiki-page.js', 'utf8');
code = code.replace(
  /  async _fetchPage\(\) \{\n    this.#pendingFields = {};\n    this.#lastMetaAttempt = null;\n    this._metaError = null;/,
  `  async _fetchPage() {
    this.#mutex.reload();
    this._syncMutexState();`
);
fs.writeFileSync('components/wiki-page.js', code, 'utf8');

let mutexCode = fs.readFileSync('lib/wiki-page-write-mutex.js', 'utf8');
mutexCode = mutexCode.replace(
  'return { ok: true, data: res.data, flushData: flushRes?.data };',
  'return { ok: true, data: res.data, flushData: flushRes ? flushRes.data : undefined };'
);
fs.writeFileSync('lib/wiki-page-write-mutex.js', mutexCode, 'utf8');
