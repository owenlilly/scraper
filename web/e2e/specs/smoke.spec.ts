import { expect, test } from "@playwright/test";
import { fakeModelClient } from "../support/fake-model";
import { env } from "../support/env";

test("the stack is up and the only model in reach is the fake one", async () => {
  const model = fakeModelClient(env.model);
  await model.reset(); // other specs share this model and leave what they asked it behind
  expect((await fetch(`${env.api}/health`)).status).toBe(200);
  expect((await fetch(`${env.model}/api/v0/models`)).status).toBe(200);
  expect(await model.requests()).toEqual([]);
});
