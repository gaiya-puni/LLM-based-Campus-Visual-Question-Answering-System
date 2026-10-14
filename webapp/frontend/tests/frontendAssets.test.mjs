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

test('favicon points to a public asset and preserves the existing logo bytes', async () => {
  const html = await readFile(new URL('../index.html', import.meta.url), 'utf8');
  assert.match(html, /rel="icon"[^>]*href="\.\/ecnu_logo\.png"/);
  const favicon = await readFile(new URL('../public/ecnu_logo.png', import.meta.url));
  const original = await readFile(new URL('../src/views/icons/ecnu_logo.png', import.meta.url));
  assert.deepEqual(favicon, original);
});
