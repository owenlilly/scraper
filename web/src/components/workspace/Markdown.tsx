"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const SAFE_LINK = /^(https?:|mailto:)/i;

/**
 * The agent's answer can quote text from any web page, so it is shown as formatted text and nothing more: no raw HTML, no images
 * (a picture's address could carry what the page said to someone else's server), and only ordinary links, opened in a new tab.
 */
export default function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      skipHtml
      disallowedElements={["img"]}
      components={{
        a: ({ href, children }) =>
          href && SAFE_LINK.test(href) ? (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ) : (
            <>{children}</>
          ),
      }}
    >
      {text}
    </ReactMarkdown>
  );
}
