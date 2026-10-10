/** The addresses scripts/e2e-ui.sh sets for the browser tests. */
const need = (name: string): string => {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set. Run the browser tests with \`npm run e2e:ui\`, which starts the stack and sets it.`);
  return value;
};

export const env = {
  get web() {
    return need("WEB_URL");
  },
  get api() {
    return need("API_URL");
  },
  get model() {
    return need("FAKE_MODEL_URL");
  },
  get webLog() {
    return need("WEB_LOG");
  },
  get database() {
    return need("DATABASE_URL");
  },
};
