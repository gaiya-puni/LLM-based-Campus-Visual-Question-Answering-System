import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { compileScript, parse } from 'vue/compiler-sfc';
import * as icons from '@element-plus/icons-vue';

test('sidebar icons are imported locally and compiled without global resolution', async () => {
  const source = await readFile(new URL('../src/views/sidebar.vue', import.meta.url), 'utf8');
  const { descriptor } = parse(source);
  const compiled = compileScript(descriptor, { id: 'sidebar-assets-test', inlineTemplate: true });
  const names = ['House', 'MapLocation', 'Menu', 'Notebook', 'ChatDotRound', 'DocumentChecked', 'DataAnalysis'];
  for (const name of names) {
    assert.ok(icons[name], `${name} exists in the installed icon library`);
    assert.ok(compiled.bindings[name]?.startsWith('setup-'), `${name} is a local setup binding`);
    assert.ok(!compiled.content.includes(`_resolveComponent("${name}")`), `${name} requires no global registration`);
  }
});

test('main entry no longer imports or globally registers the entire icon library', async () => {
  const source = await readFile(new URL('../src/main.ts', import.meta.url), 'utf8');
  assert.ok(!source.includes('ElementPlusIconsVue'));
  assert.ok(!source.includes('@element-plus/icons-vue'));
});

test('browser and iOS titles use CampusMind and reference anonymous public icons', async () => {
  const html = await readFile(new URL('../index.html', import.meta.url), 'utf8');
  assert.match(html, /<title>知行校园 CampusMind<\/title>/);
  assert.match(html, /rel="icon"[^>]*href="\.\/icons\/campusmind\.png"/);
  assert.match(html, /name="apple-mobile-web-app-title" content="知行校园 CampusMind"/);
  assert.match(html, /rel="apple-touch-icon" href="\.\/icons\/campusmind-180\.png"/);
  assert.doesNotMatch(html, /ECNU|华东师范|ecnu[-_]logo/);
  for (const [name, size] of [['campusmind', 64], ['campusmind-180', 180]]) {
    const png = await readFile(new URL(`../public/icons/${name}.png`, import.meta.url));
    assert.deepEqual([...png.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
    assert.equal(png.readUInt32BE(16), size);
    assert.equal(png.readUInt32BE(20), size);
  }
});

test('desktop and mobile branding pages compile without school emblems or school names', async () => {
  for (const file of ['App.vue', 'views/sidebar.vue', 'views/chatbot.vue', 'views/index.vue']) {
    const source = await readFile(new URL(`../src/${file}`, import.meta.url), 'utf8');
    assert.match(source, /知行校园 CampusMind/);
    assert.doesNotMatch(source, /ECNU|华东师范|华师学子|ecnu[-_]logo/);
    const { descriptor, errors } = parse(source);
    assert.deepEqual(errors, []);
    compileScript(descriptor, { id: `campusmind-${file}`, inlineTemplate: true });
  }
  const mobile = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8');
  assert.match(mobile, /src="\.\/views\/icons\/tree\.png" alt="知行校园图标"/);
  const sidebar = await readFile(new URL('../src/views/sidebar.vue', import.meta.url), 'utf8');
  assert.match(sidebar, /src="\.\/icons\/tree\.png" alt="知行校园图标"/);
  const home = await readFile(new URL('../src/views/index.vue', import.meta.url), 'utf8');
  assert.match(home, /知行校园 CampusMind——基于大模型与地图交互的校园可视问答系统/);
  assert.match(home, /漫步在大学校园/);
});

test('PWA and Android shell use CampusMind with correctly sized tree icons', async () => {
  const manifest = JSON.parse(await readFile(new URL('../public/manifest.webmanifest', import.meta.url), 'utf8'));
  assert.equal(manifest.name, '知行校园 CampusMind');
  assert.equal(manifest.short_name, manifest.name);
  assert.doesNotMatch(manifest.description, /ECNU|华东师范/);
  for (const icon of manifest.icons) {
    assert.match(icon.src, /^\.\/icons\/campusmind-(192|512)\.png$/);
    const png = await readFile(new URL(`../public/${icon.src}`, import.meta.url));
    assert.equal(icon.sizes, `${png.readUInt32BE(16)}x${png.readUInt32BE(20)}`);
  }
  const android = JSON.parse(await readFile(new URL('../../../mobile-app/manifest.json', import.meta.url), 'utf8'));
  const pages = JSON.parse(await readFile(new URL('../../../mobile-app/pages.json', import.meta.url), 'utf8'));
  assert.equal(android.name, manifest.name);
  assert.doesNotMatch(android.description, /ECNU|华东师范/);
  assert.equal(pages.globalStyle.navigationBarTitleText, manifest.name);
  assert.equal(pages.pages[0].style.navigationBarTitleText, manifest.name);
  const pwaIcon = await readFile(new URL('../public/icons/campusmind-512.png', import.meta.url));
  const androidIcon = await readFile(new URL('../../../mobile-app/static/logo.png', import.meta.url));
  assert.deepEqual(androidIcon, pwaIcon);
  const sw = await readFile(new URL('../public/sw.js', import.meta.url), 'utf8');
  assert.match(sw, /const CACHE = 'campusmind-cache-v2'/);
});

test('heatmap examples are anonymous while campus data retains original query identities', async () => {
  const source = await readFile(new URL('../src/views/campusBuild.vue', import.meta.url), 'utf8');
  assert.match(source, /placeholder="例如：帮我做XX大学XX校区的约会热力图"/);
  assert.match(source, /例如“XX大学XX校区拍照热力图”/);
  assert.doesNotMatch(source, /华东师范大学|同济大学|source-hint/);
  const config = await readFile(new URL('../src/campusConfig.ts', import.meta.url), 'utf8');
  assert.match(config, /name: "普陀"/);
  assert.match(config, /name: "闵行"/);
});
