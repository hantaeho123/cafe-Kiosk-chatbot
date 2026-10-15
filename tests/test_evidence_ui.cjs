// Run with: node --test tests/test_evidence_ui.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const code = fs.readFileSync('web/app.js', 'utf8');
const context = vm.createContext({
  t: (key, vars = {}) => `${key}${vars.n == null ? '' : `:${vars.n}`}`,
  el: (tag, attrs = {}, ...children) => ({ tag, attrs, children: children.flat(Infinity).filter(x => x != null && x !== false) }),
  verifiedRow: () => null,
});
vm.runInContext(code.slice(code.indexOf('function evidence('), code.indexOf('// 메뉴끼리 비교해')), context);
const source = (id, score) => ({ id, title: id, score, match: 'vector' });
const render = (overrides = {}) => context.evidence({
  sources: [source('unrelated', .8), source('taro', .2), source('black', .19)],
  tools: [], attribution: { status: 'reported', ids: ['black', 'taro'] }, ...overrides,
});
const find = (tree, predicate) => {
  if (!tree || typeof tree !== 'object') return [];
  return [...(predicate(tree) ? [tree] : []), ...tree.children.flatMap(child => find(child, predicate))];
};
const titles = tree => find(tree, n => n.attrs.class === 'source-title').map(n => n.children[0]);

test('reported references precede higher-scoring unused results, which start collapsed', () => {
  const tree = render();
  assert.equal(tree.attrs.open, true);
  assert.deepEqual(titles(tree), ['black', 'taro', 'unrelated']);
  const other = find(tree, n => n.attrs.class === 'evidence-other')[0];
  assert.equal(other.attrs.open, undefined);
  assert.deepEqual(titles(other), ['unrelated']);
  assert.ok(JSON.stringify(tree).includes('0.19'));
});

test('missing metadata does not promote any retrieved document', () => {
  const tree = render({ attribution: null });
  const used = find(tree, n => n.attrs.class === 'evidence-used')[0];
  assert.deepEqual(titles(used), []);
  assert.ok(JSON.stringify(used).includes('evidence_unavailable'));
  assert.deepEqual(titles(find(tree, n => n.attrs.class === 'evidence-other')[0]), ['unrelated', 'taro', 'black']);
});

test('duplicate search hits appear once and empty sections do not render zero', () => {
  const tree = render({ sources: [source('taro', .2), source('taro', .3)], attribution: { status: 'reported', ids: ['taro'] } });
  assert.deepEqual(titles(tree), ['taro']);
  assert.equal(find(tree, n => n.attrs.class === 'evidence-other').length, 0);
  assert.equal(tree.children.includes(0), false);
});
