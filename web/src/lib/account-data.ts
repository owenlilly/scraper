/** Asking the Python API to delete everything it holds about a user. Called before the account itself is removed. */

export class AccountDataError extends Error {
  constructor(public readonly status: number) {
    super(status === 409 ? "A run is still stopping" : status === 0 ? "Could not reach the service" : `The service answered ${status}`);
  }
}

export type AccountDataClient = { delete(userId: string): Promise<void> };

export function accountDataClient(options: { apiUrl: string; serviceSecret: string; fetchImpl?: typeof fetch; timeoutMs?: number }): AccountDataClient {
  const { apiUrl, serviceSecret, fetchImpl = fetch, timeoutMs = 15_000 } = options;
  return {
    async delete(userId) {
      let response: Response;
      try {
        response = await fetchImpl(`${apiUrl}/account-data`, {
          method: "DELETE",
          headers: { "content-type": "application/json", "x-service-secret": serviceSecret },
          body: JSON.stringify({ user_id: userId }),
          signal: AbortSignal.timeout(timeoutMs),
        });
      } catch {
        throw new AccountDataError(0);
      }
      if (response.status !== 204) throw new AccountDataError(response.status);
    },
  };
}
