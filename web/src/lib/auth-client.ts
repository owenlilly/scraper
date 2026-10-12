import { adminClient, jwtClient } from "better-auth/client/plugins";
import { createAuthClient } from "better-auth/react";

/** The browser's way to talk to Better Auth on this same site (`/api/auth`). Only imported by client components. */
export const authClient = createAuthClient({ plugins: [jwtClient(), adminClient()] });
