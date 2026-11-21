const base = require('./app.json').expo;

const variant = process.env.APP_VARIANT ?? 'production';
const isDev = variant === 'development';

module.exports = {
  ...base,
  name: isDev ? 'DecafClaw Dev' : 'DecafClaw',
  android: {
    ...base.android,
    package: isDev
      ? 'com.lmorchard.decafclawvoice.dev'
      : 'com.lmorchard.decafclawvoice',
  },
};
