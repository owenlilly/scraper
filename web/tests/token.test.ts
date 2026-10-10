import { createPublicKey, verify } from "node:crypto";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { BASE, makeHarness, type Harness } from "./harness";

let h: Harness;
let cookie: string;
let userId: string;
let admin: string;
beforeAll(async () => {
  h = await makeHarness();
  admin = await h.makeAdmin();
  cookie = await h.makeUser("tina@example.com");
  userId = (await h.userIdOf("tina@example.com"))!;
});
afterAll(() => h.pool.end());

const decode = (part: string) => JSON.parse(Buffer.from(part, "base64url").toString());
const getToken = async () => ((await (await h.call("/token", { cookie })).json()) as { token: string }).token;

describe("the token the browser sends to the Python API", () => {
  it("is a short-lived EdDSA token that says who the person is and nothing else", async () => {
    const token = await getToken();
    const [header, payload] = token.split(".").slice(0, 2).map(decode);
    expect(header.alg).toBe("EdDSA");
    expect(header.kid).toBeTruthy();
    expect(Object.keys(payload).sort()).toEqual(["aud", "exp", "iat", "iss", "sub"]);
    expect(payload.sub).toBe(userId);
    expect(payload.iss).toBe(BASE);
    expect(payload.aud).toBe("http://api.test");
    expect(payload.exp - payload.iat).toBe(15 * 60);
  });

  it("carries no email address or name, which would travel to the API on every request", async () => {
    const token = await getToken();
    const text = Buffer.from(token.split(".")[1], "base64url").toString();
    expect(text).not.toContain("tina");
    expect(text).not.toContain("@");
  });

  it("is given only to someone who is signed in", async () => {
    expect((await h.call("/token")).status).toBe(401);
    expect((await h.call("/token", { cookie: "better-auth.session_token=forged.value" })).status).toBe(401);
  });

  it("stops being given to someone who is banned, even though they were signed in", async () => {
    const other = await h.makeUser("banned@example.com");
    expect((await h.call("/token", { cookie: other })).status).toBe(200);
    const id = (await h.userIdOf("banned@example.com"))!;
    expect((await h.call("/admin/ban-user", { cookie: admin, body: { userId: id } })).status).toBe(200);
    expect((await h.call("/token", { cookie: other })).status).toBe(401);
  });
});

describe("the published key set", () => {
  it("holds the signing key, as a public Ed25519 key with no private part", async () => {
    const response = await h.call("/jwks");
    expect(response.status).toBe(200);
    const { keys } = (await response.json()) as { keys: Record<string, string>[] };
    const [header] = (await getToken()).split(".").slice(0, 1).map(decode);
    const key = keys.find((k) => k.kid === header.kid)!;
    expect(key).toMatchObject({ kty: "OKP", crv: "Ed25519" });
    expect(key.d).toBeUndefined();
    expect(JSON.stringify(keys)).not.toMatch(/"d"|privateKey/);
  });

  it("checks out a token with nothing but the standard library, as the Python API will", async () => {
    const token = await getToken();
    const [head, body, signature] = token.split(".");
    const { keys } = (await (await h.call("/jwks")).json()) as { keys: (Record<string, string> & { kid: string })[] };
    const jwk = keys.find((k) => k.kid === decode(head).kid)!;
    const publicKey = createPublicKey({ key: jwk as never, format: "jwk" });
    expect(verify(null, Buffer.from(`${head}.${body}`), publicKey, Buffer.from(signature, "base64url"))).toBe(true);
    expect(verify(null, Buffer.from(`${head}.${body}x`), publicKey, Buffer.from(signature, "base64url"))).toBe(false);
  });

  it("keeps the same key between tokens, so the API's cache stays valid", async () => {
    const a = decode((await getToken()).split(".")[0]).kid;
    const b = decode((await getToken()).split(".")[0]).kid;
    expect(a).toBe(b);
  });
});
