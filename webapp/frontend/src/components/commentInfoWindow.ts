/**
 * 把 `PoiCommentPanel` 挂进高德 `AMap.InfoWindow` 的控制器。
 *
 * 为什么不用「每个 marker 一个 InfoWindow + 一个 Vue 应用」：
 * 植物地图一次可能渲染上千个 marker，逐点建 Vue 应用会直接把内存吃穿。
 * 因此这里维护**一个共享弹窗**：点击哪个点就把它打开在哪个点位上，
 * 每次打开重建一次 Vue 应用（同一时刻只有一个），关闭即卸载。
 *
 * 弹窗内容用 DOM 节点而不是 HTML 字符串：`AMap.InfoWindow` 支持 DOM 节点 content，
 * 这样内部就是一段真正的 Vue 应用，滚动、分页、乐观更新才成立（字符串方案做不到）。
 */

import { createApp, type App } from 'vue';
import PoiCommentPanel from './PoiCommentPanel.vue';

export interface CommentWindowContent {
  /**
   * 点位唯一标识（与后端 POI 库一致）。
   * 传空字符串表示这个打点没落到真实点位上（如无法回落的合成聚类点），
   * 此时只显示 `header`，不挂留言板。
   */
  poiId?: string;
  /** 点位名，仅用于标题展示 */
  poiName?: string;
  /** 弹窗顶部的自定义内容（如"步行导航到这里"按钮）；由调用方负责转义 */
  header?: {
    html: string;
    bind?: (root: HTMLElement) => void;
  };
}

export interface CommentInfoWindowController {
  infoWindow: any;
  /** 在指定坐标打开弹窗并挂载评论区 */
  open: (map: any, position: any, content: CommentWindowContent) => void;
  close: () => void;
  /** 页面卸载时调用：卸载 Vue 应用并断开 DOM，避免内存泄漏 */
  destroy: () => void;
}

export function createCommentInfoWindowController(
  AMap: any,
  offsetY = -34,
): CommentInfoWindowController {
  const container = document.createElement('div');
  container.className = 'poi-comment-window';

  const headerEl = document.createElement('div');
  headerEl.className = 'poi-comment-window__header';
  headerEl.style.display = 'none';

  const mountEl = document.createElement('div');
  container.append(headerEl, mountEl);

  const infoWindow = new AMap.InfoWindow({
    content: container,
    offset: new AMap.Pixel(0, offsetY),
  });

  let app: App | null = null;

  const unmountApp = () => {
    if (app) {
      app.unmount();
      app = null;
    }
  };

  return {
    infoWindow,

    open(map, position, content) {
      unmountApp();
      headerEl.innerHTML = '';
      if (content.header?.html) {
        headerEl.innerHTML = content.header.html;
        content.header.bind?.(headerEl);
        headerEl.style.display = '';
      } else {
        headerEl.style.display = 'none';
      }
      mountEl.innerHTML = '';
      mountEl.hidden = !content.poiId;
      if (content.poiId) {
        app = createApp(PoiCommentPanel, {
          poiId: content.poiId,
          poiName: content.poiName,
        });
        app.mount(mountEl);
      }
      // 内容尺寸变了要重新交回高德，否则弹窗定位仍按旧尺寸计算
      infoWindow.setContent(container);
      infoWindow.open(map, position);
    },

    close() {
      infoWindow.close();
    },

    destroy() {
      unmountApp();
      headerEl.innerHTML = '';
      mountEl.innerHTML = '';
      infoWindow.close();
      infoWindow.setContent('');
    },
  };
}
