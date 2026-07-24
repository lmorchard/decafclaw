// Ensure localStorage is available in jsdom environment
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
