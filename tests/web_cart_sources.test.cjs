const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('recipe ingredient shares retain unknown amounts and convert only display formatting', () => {
  const context = vm.createContext({ window: {} });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/static/features/cart.js'), 'utf8'), context);
  const cart = context.window.RezepteFeatures.cart();
  assert.equal(cart.cartSourceQuantity({ amount: null, unit: 'g' }), 'Mengenanteil unbekannt');
  assert.equal(cart.cartSourceQuantity({ amount: 0.5, unit: 'kg' }), '0,5 kg');
  assert.equal(cart.cartSourceQuantity({ amount: 0, unit: 'g' }), '0 g');
});
