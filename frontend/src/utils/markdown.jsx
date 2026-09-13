// WHY: Fences are the ONLY structural truth in our format. We parse
// line-by-line with a state machine: inside a fence, every line is code
// (blank lines included); outside, lines accumulate as text.
const FENCE = "`".repeat(3);

// WHY: the synthesizer emits each file's path as a pure-bold line
// (**app.py**) immediately before its python fence.
function isPureBold(text) {
  return /^\*\*[^*]+\*\*$/.test(text.trim());
}

function stripBold(text) {
  return text.trim().slice(2, -2);
}

function parseSegments(markdown) {
  const segments = [];
  const lines = markdown.split("\n");

  let inCode = false;
  let codeLang = "";
  let codeBuffer = [];
  let textBuffer = [];
  let pendingFilename = null;

  // WHY: filename detection happens AT fence-open time, looking at the
  // text buffer's last non-empty line. The old post-hoc segment merge
  // failed for the FIRST file: its bold header rode along with the
  // explanation paragraph inside ONE text segment, so the purity check
  // never matched and the header leaked into the paragraph text.
  const extractPendingFilename = () => {
    for (let i = textBuffer.length - 1; i >= 0; i--) {
      const t = textBuffer[i].trim();
      if (t === "") continue; // skip blank lines above the header
      if (isPureBold(t)) {
        pendingFilename = stripBold(t);
        textBuffer = textBuffer.slice(0, i); // header leaves the text
      }
      break; // first non-empty line from the bottom decides
    }
  };

  const flushText = () => {
    const text = textBuffer.join("\n").trim();
    if (text) segments.push({ type: "text", content: text });
    textBuffer = [];
  };

  const emitCode = () => {
    const seg = {
      type: codeLang === "python" ? "code" : "output",
      content: codeBuffer.join("\n").trim(),
    };
    if (pendingFilename) {
      seg.filename = pendingFilename;
      pendingFilename = null;
    }
    segments.push(seg);
  };

  for (const line of lines) {
    const trimmed = line.trim();

    if (trimmed.startsWith(FENCE)) {
      if (!inCode) {
        codeLang = trimmed.slice(FENCE.length).trim();
        // WHY python fences only: '**✅ Verified output:**' precedes the
        // output fence and is ALSO pure bold - extracting it would delete
        // the visible label from the answer.
        if (codeLang === "python") {
          extractPendingFilename();
        }
        flushText();
        inCode = true;
        codeBuffer = [];
      } else {
        emitCode();
        inCode = false;
        codeLang = "";
        codeBuffer = [];
      }
      continue;
    }

    if (inCode) {
      codeBuffer.push(line);
    } else {
      textBuffer.push(line);
    }
  }

  // WHY: Safety net - if the stream ever dies mid-code, render what we
  // have instead of silently dropping it.
  if (inCode && codeBuffer.length) {
    emitCode();
  } else {
    flushText();
  }

  return segments;
}

// WHY: Renders **bold** inline without a markdown engine.
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
            <span className="text-xs text-gray-400">{segment.filename || "main.py"}</span>
            <button
              onClick={() => navigator.clipboard.writeText(segment.content)}
              title={`Copy ${segment.filename || "code"}`}
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