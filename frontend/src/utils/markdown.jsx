// WHY: Fences are the ONLY structural truth in our format. We parse
// line-by-line with a state machine: inside a fence, every line is code
// (blank lines included); outside, lines accumulate as text.
// The old blank-line splitter shattered code blocks that contained
// blank lines — a format assumption reality violated.
const FENCE = "`".repeat(3);

function parseSegments(markdown) {
  const segments = [];
  const lines = markdown.split("\n");

  let inCode = false;
  let codeLang = "";
  let codeBuffer = [];
  let textBuffer = [];

  const flushText = () => {
    const text = textBuffer.join("\n").trim();
    if (text) segments.push({ type: "text", content: text });
    textBuffer = [];
  };

  for (const line of lines) {
    const trimmed = line.trim();

    if (trimmed.startsWith(FENCE)) {
      if (!inCode) {
        // Opening fence — flush any pending text, start capturing code
        flushText();
        inCode = true;
        codeLang = trimmed.slice(FENCE.length).trim(); // "python" or ""
        codeBuffer = [];
      } else {
        // Closing fence — emit the complete code segment
        segments.push({
          type: codeLang === "python" ? "code" : "output",
          content: codeBuffer.join("\n").trim(),
        });
        inCode = false;
        codeLang = "";
        codeBuffer = [];
      }
      continue;
    }

    if (inCode) {
      // WHY: push the ORIGINAL line, not trimmed — indentation is code.
      codeBuffer.push(line);
    } else {
      textBuffer.push(line);
    }
  }

  // WHY: Safety net — if the stream ever dies mid-code, render what we
  // have instead of silently dropping it.
  if (inCode && codeBuffer.length) {
    segments.push({
      type: codeLang === "python" ? "code" : "output",
      content: codeBuffer.join("\n").trim(),
    });
  } else {
    flushText();
  }

  return segments;
}

// WHY: Renders **bold** inline without a markdown engine. split() with a
// capture group keeps the **...** chunks in the array; odd indices = bold.
function renderInline(text, keyPrefix) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={`${keyPrefix}-${i}`} className="font-semibold text-gray-100">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return <span key={`${keyPrefix}-${i}`}>{part}</span>;
  });
}

export function renderMarkdown(markdown) {
  if (!markdown) return null;

  return parseSegments(markdown).map((segment, i) => {
    if (segment.type === "code") {
      return (
        <div key={i} className="my-3">
          <div className="flex items-center justify-between bg-gray-800 border border-gray-700 border-b-0 rounded-t-lg px-3 py-1.5">
            <span className="text-xs text-gray-400">main.py</span>
            <button
              onClick={() => navigator.clipboard.writeText(segment.content)}
              className="text-xs text-gray-400 hover:text-gray-100 transition-colors"
            >
              Copy
            </button>
          </div>
          <pre className="bg-black border border-gray-700 border-t-0 rounded-b-lg p-3 text-xs text-green-300 overflow-x-auto">
            <code>{segment.content}</code>
          </pre>
        </div>
      );
    }

    if (segment.type === "output") {
      // WHY: NO hardcoded label here. The synthesizer's own
      // "**Verified output:**" bold text renders as the label — one source
      // of truth. Hardcoding it caused the doubled label you saw.
      return (
        <pre
          key={i}
          className="bg-black/60 border border-gray-800 rounded-lg p-3 my-2 text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap"
        >
          {segment.content}
        </pre>
      );
    }

    return (
      <p key={i} className="text-sm text-gray-300 leading-relaxed my-1">
        {renderInline(segment.content, i)}
      </p>
    );
  });
}