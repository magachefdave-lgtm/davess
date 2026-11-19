const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const picker = fs.readFileSync(path.join(__dirname, '../../frontend/dist/design_picker.js'), 'utf8');

function firstPaint(month, saved, storageBlocked = false) {
  const dataset = {theme: 'dark'};
  vm.runInNewContext(picker, {
    Date: class extends Date { constructor() { super(2026, month - 1, 1); } },
    document: {documentElement: {dataset}, addEventListener() {}},
    localStorage: {getItem() {
      if (storageBlocked) throw new Error('Storage blocked');
      return saved;
    }},
  });
  assert.equal(dataset.theme, 'dark');
  return dataset.winter || 'original';
}

test('first paint follows the local calendar when the saved design is automatic or unavailable', () => {
  for (let month = 1; month <= 12; month++) {
    const expected = month >= 11 ? 'christmas' : 'original';
    for (const saved of [null, 'auto', 'obsolete']) assert.equal(firstPaint(month, saved), expected);
    assert.equal(firstPaint(month, null, true), expected);
  }
});

test('explicit designs saved by earlier versions keep precedence in every season', () => {
  for (const saved of ['original', 'frost', 'paper', 'print', 'photo', 'christmas']) {
    for (const month of [1, 10, 11, 12]) assert.equal(firstPaint(month, saved), saved);
  }
});
