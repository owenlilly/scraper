import { startFakeModel } from "./fake-model";

startFakeModel(Number(process.env.FAKE_MODEL_PORT ?? 8190)).then((model) => console.log(`fake model listening on ${model.url}`));
