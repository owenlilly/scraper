import { SignOutButton } from "../components/SignOutButton";
import { requireSession } from "../lib/session";

// A stand-in until the workspace is built in a later task.
export default async function Home() {
  const session = await requireSession();
  return (
    <main className="placeholder">
      <h1>Signed in as {session.user.email}</h1>
      <div>
        <SignOutButton />
      </div>
    </main>
  );
}
