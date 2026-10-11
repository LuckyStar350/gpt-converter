(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LocalArchive = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const encoder = new TextEncoder();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  const MAX_SIZE = 64 * 1024 * 1024;
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let value = i;
    for (let bit = 0; bit < 8; bit++) value = (value & 1) ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    table[i] = value >>> 0;
  }
  function crc32(bytes) {
    let crc = 0xffffffff;
    for (const byte of bytes) crc = table[(crc ^ byte) & 255] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  }
  function concat(parts) {
    const result = new Uint8Array(parts.reduce((size, part) => size + part.length, 0));
    let offset = 0;
    for (const part of parts) { result.set(part, offset); offset += part.length; }
    return result;
  }
  // Standard ZIP with stored entries: no third-party library or network dependency.
  function createZip(files) {
    if (files.length > 5000) throw new Error('ZIP 最多支持 5000 个文件');
    const locals = [];
    const central = [];
    let offset = 0;
    let total = 0;
    for (const file of files) {
      const name = encoder.encode(file.name);
      const data = encoder.encode(file.content);
      total += data.length;
      if (total > MAX_SIZE) throw new Error('导出 ZIP 的内容超过 64 MB，请分批导出');
      const checksum = crc32(data);
      const header = new Uint8Array(30 + name.length);
      const view = new DataView(header.buffer);
      view.setUint32(0, 0x04034b50, true);
      view.setUint16(4, 20, true);
      view.setUint16(6, 0x0800, true);
      view.setUint16(12, 0x21, true); // 1980-01-01, valid minimum DOS date.
      view.setUint32(14, checksum, true);
      view.setUint32(18, data.length, true);
      view.setUint32(22, data.length, true);
      view.setUint16(26, name.length, true);
      header.set(name, 30);
      locals.push(header, data);
      const directory = new Uint8Array(46 + name.length);
      const directoryView = new DataView(directory.buffer);
      directoryView.setUint32(0, 0x02014b50, true);
      directoryView.setUint16(4, 20, true);
      directoryView.setUint16(6, 20, true);
      directoryView.setUint16(8, 0x0800, true);
      directoryView.setUint16(14, 0x21, true);
      directoryView.setUint32(16, checksum, true);
      directoryView.setUint32(20, data.length, true);
      directoryView.setUint32(24, data.length, true);
      directoryView.setUint16(28, name.length, true);
      directoryView.setUint32(42, offset, true);
      directory.set(name, 46);
      central.push(directory);
      offset += header.length + data.length;
    }
    const directorySize = central.reduce((size, part) => size + part.length, 0);
    const end = new Uint8Array(22);
    const view = new DataView(end.buffer);
    view.setUint32(0, 0x06054b50, true);
    view.setUint16(8, files.length, true);
    view.setUint16(10, files.length, true);
    view.setUint32(12, directorySize, true);
    view.setUint32(16, offset, true);
    return concat([...locals, ...central, end]);
  }

  async function readZip(buffer) {
    const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    function range(offset, length) {
      if (offset < 0 || length < 0 || offset + length > bytes.length) throw new Error('ZIP 文件损坏或不完整');
    }
    let end = -1;
    for (let position = bytes.length - 22; position >= Math.max(0, bytes.length - 65557); position--) {
      if (view.getUint32(position, true) === 0x06054b50 && position + 22 + view.getUint16(position + 20, true) === bytes.length) { end = position; break; }
    }
    if (end < 0) throw new Error('无法识别 ZIP；不支持 ZIP64 或分卷压缩包');
    if (view.getUint16(end + 4, true) || view.getUint16(end + 6, true)) throw new Error('不支持分卷 ZIP');
    const count = view.getUint16(end + 10, true);
    if (count === 65535 || count > 5000) throw new Error('不支持 ZIP64 或超过 5000 个文件的 ZIP');
    let position = view.getUint32(end + 16, true);
    const files = [];
    let total = 0;
    for (let i = 0; i < count; i++) {
      range(position, 46);
      if (view.getUint32(position, true) !== 0x02014b50) throw new Error('ZIP 目录损坏');
      const flags = view.getUint16(position + 8, true);
      const method = view.getUint16(position + 10, true);
      const checksum = view.getUint32(position + 16, true);
      const compressedSize = view.getUint32(position + 20, true);
      const size = view.getUint32(position + 24, true);
      const nameLength = view.getUint16(position + 28, true);
      const extraLength = view.getUint16(position + 30, true);
      const commentLength = view.getUint16(position + 32, true);
      const localOffset = view.getUint32(position + 42, true);
      range(position + 46, nameLength + extraLength + commentLength);
      const name = new TextDecoder().decode(bytes.subarray(position + 46, position + 46 + nameLength));
      position += 46 + nameLength + extraLength + commentLength;
      if (!/\.(json|jsonl|txt)$/i.test(name) || name.startsWith('__MACOSX/')) continue;
      if (flags & 1) throw new Error('不支持加密 ZIP，请先解密');
      if (size === 0xffffffff || localOffset === 0xffffffff || compressedSize === 0xffffffff) throw new Error('不支持 ZIP64');
      total += size;
      if (total > MAX_SIZE || size > 20 * 1024 * 1024) throw new Error('ZIP 解压内容过大：单文件最多 20 MB，总计最多 64 MB');
      range(localOffset, 30);
      if (view.getUint32(localOffset, true) !== 0x04034b50) throw new Error('ZIP 文件头损坏');
      const start = localOffset + 30 + view.getUint16(localOffset + 26, true) + view.getUint16(localOffset + 28, true);
      range(start, compressedSize);
      const compressed = bytes.subarray(start, start + compressedSize);
      let data;
      if (method === 0) data = compressed;
      else if (method === 8) {
        if (typeof DecompressionStream === 'undefined') throw new Error('此浏览器不能解压 ZIP，请使用新版 Chrome / Edge 或先解压后导入');
        const reader = new Blob([compressed]).stream().pipeThrough(new DecompressionStream('deflate-raw')).getReader();
        const chunks = [];
        let actualSize = 0;
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            actualSize += value.length;
            if (actualSize > size || actualSize > 20 * 1024 * 1024) { await reader.cancel(); throw new Error('ZIP 解压大小与声明不符'); }
            chunks.push(value);
          }
        } finally { reader.releaseLock(); }
        data = concat(chunks);
      } else throw new Error('ZIP 压缩方式不支持，请先解压后导入');
      if (data.length !== size || crc32(data) !== checksum) throw new Error('ZIP 校验失败，文件可能已损坏');
      files.push({ name, content: decoder.decode(data).replace(/^\uFEFF/, '') });
    }
    if (!files.length) throw new Error('ZIP 内没有 JSON、JSONL 或 TXT 文件');
    return files;
  }
  return { createZip, readZip, crc32 };
});
