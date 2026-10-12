import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    fileParallelism: false, // the database tests share one schema
    testTimeout: 20_000,
    hookTimeout: 30_000,
  },
});
