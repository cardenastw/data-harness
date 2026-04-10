import { useState } from "react";
import type { DocResult } from "../types";

interface Props {
  docs: DocResult[];
}

export default function DocsBlock({ docs }: Props) {
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  if (!docs.length) {
    return null;
  }

  return (
    <div className="docs-block">
      <div className="docs-block-header">
        Sources ({docs.length})
      </div>
      <ul className="docs-list">
        {docs.map((doc, i) => {
          const isOpen = openIndex === i;
          return (
            <li key={doc.path} className="docs-item">
              <button
                className="docs-item-header"
                onClick={() => setOpenIndex(isOpen ? null : i)}
                type="button"
              >
                <span className="docs-item-title">{doc.title}</span>
                <span className="docs-item-path">{doc.path}</span>
              </button>
              {!isOpen && doc.snippet && (
                <p className="docs-item-snippet">{doc.snippet}</p>
              )}
              {isOpen && (
                <pre className="docs-item-content">{doc.content}</pre>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
