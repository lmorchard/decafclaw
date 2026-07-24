// jsdom does implement localStorage, but Vitest's jsdom environment doesn't
// propagate jsdom's prototype-getter `localStorage` onto the Node global, and
// reading the bare global otherwise triggers Node's `localStorage`
// ExperimentalWarning. This shim closes that global-propagation gap.
const storage = {};
global.localStorage = {
  getItem: (key) => (key in storage ? storage[key] : null),
  setItem: (key, value) => {
    storage[key] = String(value);
  },
  removeItem: (key) => {
    delete storage[key];
  },
  clear: () => {
    for (const key in storage) delete storage[key];
  },
  key: (index) => Object.keys(storage)[index] || null,
  get length() {
    return Object.keys(storage).length;
  },
};
