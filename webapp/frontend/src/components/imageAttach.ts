/**
 * 图片附件：选择 / 拖拽 / 粘贴 / 拍照 → 本地压缩 → 产出 `/api/chat` 可直接使用的 payload。
 *
 * **口径必须与后端同源，改一处就要改两处**：
 * - `webapp/backend/api/chat_api.py`：`MAX_ATTACHMENTS` / `MAX_ATTACHMENT_BYTES` / `ATTACHMENT_MIMES`
 * - `webapp/backend/flask_app.py`：`ATTACHMENTS_BODY_BUDGET`（请求体总上限，默认 12MB）
 *
 * 2 张 × 4MB 原始数据 base64 后约 10.7MB，仍在上限之内，因此前端只需按"单张体积 + 张数"把关；
 * 常规手机照片几百 KB~几 MB，经过这里的长边压缩后通常只剩几百 KB。
 */

/** 与后端 `MAX_ATTACHMENTS` 一致：单轮最多几张。 */
export const MAX_ATTACHMENTS = 2;
/** 与后端 `MAX_ATTACHMENT_BYTES` 一致：单张原始字节上限（base64 解码后）。 */
export const MAX_ATTACHMENT_BYTES = 4 * 1024 * 1024;
/** 与后端 `ATTACHMENT_MIMES` 一致。 */
export const ALLOWED_MIMES: readonly string[] = ['image/jpeg', 'image/png', 'image/webp'];

/** 压缩目标：长边像素上限与 JPEG 质量。 */
export const MAX_EDGE_PX = 1600;
export const JPEG_QUALITY = 0.82;
/** 原图不超过这个体积时直接复用，避免对截图做一次无谓的有损重编码。 */
const REUSE_ORIGINAL_BYTES = 900 * 1024;

export interface ImageAttachment {
  id: string;
  name: string;
  mime: string;
  /** `data:image/...;base64,...`，与后端 `parse_attachments` 的期望格式一致。 */
  dataUrl: string;
  bytes: number;
  sizeLabel: string;
}

export interface AttachOutcome {
  items: ImageAttachment[];
  /** 非空表示**整批失败**（不会产生任何附件）。 */
  error: string;
  /** 非空表示**部分成功**（例如超出张数被截断）。 */
  notice: string;
}

export function describeBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0KB';
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
  return `${Math.max(1, Math.round(bytes / 1024))}KB`;
}

/**
 * 由 dataURL 反推原始字节数。
 * 算法与后端 `_base64_size` 完全一致，两侧对"是否超限"的判断必须得到同一个结论。
 */
export function dataUrlBytes(dataUrl: string): number {
  const separator = dataUrl.indexOf(',');
  const payload = separator >= 0 ? dataUrl.slice(separator + 1) : '';
  const padding = payload.endsWith('==') ? 2 : payload.endsWith('=') ? 1 : 0;
  return Math.max(0, Math.floor((payload.length * 3) / 4) - padding);
}

function isAllowedMime(mime: string): boolean {
  return ALLOWED_MIMES.includes(String(mime || '').toLowerCase());
}

function newId(): string {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === 'function') return cryptoApi.randomUUID();
  return `img-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/** 从 `DataTransfer` / `input.files` 里挑出图片文件，非图片类型直接忽略。 */
export function pickImageFiles(source: FileList | File[] | null | undefined): File[] {
  if (!source) return [];
  return Array.from(source).filter((file) => !!file && isAllowedMime(file.type));
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('图片读取失败'));
    reader.readAsDataURL(file);
  });
}

function loadImage(dataUrl: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('图片解码失败'));
    image.src = dataUrl;
  });
}

/** 等比缩放到长边不超过 `MAX_EDGE_PX`，再按 JPEG 重编码（铺白底，避免透明区域变黑）。 */
async function compressToJpeg(file: File): Promise<{ dataUrl: string; mime: string }> {
  const original = await readAsDataUrl(file);
  const image = await loadImage(original);
  const longest = Math.max(image.naturalWidth, image.naturalHeight) || 1;
  const scale = Math.min(1, MAX_EDGE_PX / longest);
  const width = Math.max(1, Math.round(image.naturalWidth * scale));
  const height = Math.max(1, Math.round(image.naturalHeight * scale));

  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('当前浏览器不支持图片压缩');
  context.fillStyle = '#ffffff';
  context.fillRect(0, 0, width, height);
  context.drawImage(image, 0, 0, width, height);
  return { dataUrl: canvas.toDataURL('image/jpeg', JPEG_QUALITY), mime: 'image/jpeg' };
}

/**
 * 把文件列表转成可发送的附件。
 *
 * @param files    本次新增的文件（来自选择、拖拽或粘贴）
 * @param existing 当前已挂载的附件数量，用来判断是否超出张数上限
 */
export async function buildAttachments(files: File[], existing: number): Promise<AttachOutcome> {
  const room = MAX_ATTACHMENTS - Math.max(0, existing);
  if (room <= 0) {
    return { items: [], error: `一次最多上传 ${MAX_ATTACHMENTS} 张图片`, notice: '' };
  }
  const accepted = files.slice(0, room);
  const skipped = files.length - accepted.length;
  const items: ImageAttachment[] = [];

  for (const file of accepted) {
    let dataUrl = '';
    let mime = String(file.type || '').toLowerCase();
    try {
      if (file.size <= REUSE_ORIGINAL_BYTES) {
        dataUrl = await readAsDataUrl(file);
      } else {
        const compressed = await compressToJpeg(file);
        dataUrl = compressed.dataUrl;
        mime = compressed.mime;
      }
    } catch {
      return { items, error: `「${file.name || '图片'}」读取失败，请换一张试试`, notice: '' };
    }

    const bytes = dataUrlBytes(dataUrl);
    if (bytes > MAX_ATTACHMENT_BYTES) {
      return {
        items,
        error: `单张图片不能超过 ${MAX_ATTACHMENT_BYTES / (1024 * 1024)}MB，请压缩后重试`,
        notice: '',
      };
    }
    items.push({
      id: newId(),
      name: file.name || '照片',
      mime,
      dataUrl,
      bytes,
      sizeLabel: describeBytes(bytes),
    });
  }

  return {
    items,
    error: '',
    notice: skipped > 0 ? `已忽略超出上限的 ${skipped} 张` : '',
  };
}

/** 请求体里只带 `mime` + `dataUrl`，与后端 `parse_attachments` 的读取字段一致。 */
export function payloadForRequest(items: ImageAttachment[]): Array<{ mime: string; dataUrl: string }> {
  return items.map((item) => ({ mime: item.mime, dataUrl: item.dataUrl }));
}
