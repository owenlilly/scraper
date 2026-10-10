"use client";

import { lazy, memo, Suspense } from "react";

// The formatting library is the largest piece of the page and an answer only needs it once there is one, so it loads on demand.
const Markdown = lazy(() => import("./Markdown"));

function PlainText({ text }: { text: string }) {
  return (
    <>
      {text
        .split(/\n{2,}/)
        .filter((part) => part.trim())
        .map((part, index) => (
          <p key={index}>{part}</p>
        ))}
    </>
  );
}

// Memoized: while an answer streams, every earlier answer on screen keeps the same text and is not formatted again.
export const Prose = memo(function Prose({ text }: { text: string }) {
  return (
    <div className="prose">
      <Suspense fallback={<PlainText text={text} />}>
        <Markdown text={text} />
      </Suspense>
    </div>
  );
});
