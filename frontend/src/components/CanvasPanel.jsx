import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  X,
  Download,
  Copy,
  Check as CheckIcon,
  BarChart3,
  FileText,
  Terminal,
  Image as ImageIcon,
  Send,
} from "lucide-react";
import LazyPlot from "./LazyPlot";
import ReactMarkdown from "react-markdown";

function normalizeContentType(mime) {
  if (!mime) return "text";
  if (mime.includes("markdown")) return "markdown";
  if (mime.includes("html")) return "html";
  if (mime.startsWith("image/")) return "image";
  if (mime.includes("pdf")) return "pdf";
  return "text";
}

// Live sandbox session terminal. Connects to /ws/sandbox/interact/{id}
// and renders streamed stdout/stderr alongside an input box for stdin.
function SandboxTerminal({ sessionId }) {
  const [lines, setLines] = useState([]);
  const [input, setInput] = useState("");
  const [ready, setReady] = useState(false);
  const [exited, setExited] = useState(false);
  const wsRef = useRef(null);
  const scrollRef = useRef(null);

  useEffect(() => {
    if (!sessionId) return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(
      `${proto}//${window.location.host}/ws/sandbox/interact/${sessionId}`,
    );
    wsRef.current = ws;

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "ready") {
          setReady(true);
          return;
        }
        if (msg.type === "exit") {
          setExited(true);
          setLines((prev) => [
            ...prev,
            { kind: "system", text: `[process exited with code ${msg.code}]` },
          ]);
          return;
        }
        if (msg.type === "stdout" || msg.type === "stderr") {
          setLines((prev) => [...prev, { kind: msg.type, text: msg.data }]);
        }
      } catch {
        setLines((prev) => [...prev, { kind: "stdout", text: e.data }]);
      }
    };
    ws.onerror = () => {
      setLines((prev) => [
        ...prev,
        { kind: "stderr", text: "[connection error]" },
      ]);
    };
    ws.onclose = () => setReady(false);

    return () => {
      try {
        ws.close();
      } catch {}
    };
  }, [sessionId]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines]);

  const handleSend = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(input);
    setLines((prev) => [...prev, { kind: "input", text: `> ${input}` }]);
    setInput("");
  }, [input]);

  return (
    <div className="sandbox-terminal">
      <div className="terminal-container" ref={scrollRef}>
        {lines.map((l, i) => (
          <div
            key={i}
            className={`terminal-line ${l.kind === "stderr" ? "stderr" : ""} ${l.kind === "input" ? "input" : ""} ${l.kind === "system" ? "system" : ""}`}
          >
            {l.text}
          </div>
        ))}
        {!exited && ready && <div className="terminal-cursor" />}
      </div>
      <form
        className="sandbox-terminal-input"
        onSubmit={(e) => {
          e.preventDefault();
          handleSend();
        }}
      >
        <span className="sandbox-terminal-prompt">stdin</span>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            exited
              ? "Session ended"
              : ready
                ? "Type input and press Enter…"
                : "Connecting…"
          }
          disabled={!ready || exited}
        />
        <button
          type="submit"
          disabled={!ready || exited || !input.trim()}
          title="Send"
        >
          <Send size={13} />
        </button>
      </form>
    </div>
  );
}

export default function CanvasPanel({
  image,
  figureJson,
  content,
  contentType,
  chunks,
  filename,
  previewUrl,
  sandboxSessionId,
  title,
  onClose,
  style,
}) {
  const [iframeError, setIframeError] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [previewData, setPreviewData] = useState(null);
  const bodyRef = useRef(null);

  useEffect(() => {
    if (filename && !content) {
      fetch(`/api/preview/${encodeURIComponent(filename)}`)
        .then((r) => r.json())
        .then((data) => setPreviewData(data))
        .catch((err) => console.error("Preview fetch failed:", err));
    }
  }, [filename, content]);

  // Effective content fields — the inline-provided values win, otherwise
  // fall back to whatever /api/preview returned.
  const effectiveContent = content || previewData?.content || "";
  // Callers pass either a raw MIME type (e.g. "application/pdf" from a
  // downloadable card) or an already-normalized short type (e.g.
  // "terminal", "markdown"). Normalize only the MIME variants so the
  // render branches below — which compare against "pdf", "image", … —
  // match. Short types are passed through untouched.
  const normalizedContentType = contentType
    ? contentType.includes("/")
      ? normalizeContentType(contentType)
      : contentType
    : null;
  const effectiveContentType =
    normalizedContentType ||
    normalizeContentType(previewData?.content_type) ||
    (previewData?.kind === "pdf" ? "pdf" : null) ||
    (previewData?.kind === "image" ? "image" : null) ||
    "text";
  const effectivePreviewUrl = previewUrl || previewData?.url;

  const hasContent =
    image ||
    figureJson ||
    effectiveContent ||
    (chunks && chunks.length > 0) ||
    sandboxSessionId ||
    effectivePreviewUrl;
  if (!hasContent) return null;

  const imageUrl = image ? `/api/plots/${encodeURIComponent(image)}` : null;

  const handleDownload = async () => {
    if (image) {
      const a = document.createElement("a");
      a.href = imageUrl;
      a.download = image;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      return;
    }

    if (filename) {
      fetch(`/api/download/${encodeURIComponent(filename)}`)
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.blob();
        })
        .then((blob) => {
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = filename;
          a.style.display = "none";
          document.body.appendChild(a);
          a.click();
          setTimeout(() => {
            URL.revokeObjectURL(url);
            if (a.parentNode) a.parentNode.removeChild(a);
          }, 5000);
        })
        .catch((err) => {
          console.error("Canvas download failed:", err);
          window.open(
            `/api/download/${encodeURIComponent(filename)}`,
            "_blank",
          );
        });
      return;
    }

    // Convert Plotly JSON to PNG via backend, then download
    setDownloading(true);
    try {
      const res = await fetch("/api/plots/from-json", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ figure_json: figureJson }),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`Export failed: ${res.status} ${text}`);
      }
      const { filename: fname } = await res.json();
      const a = document.createElement("a");
      a.href = `/api/plots/${encodeURIComponent(fname)}`;
      a.download = fname;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } catch (err) {
      console.error("Plotly PNG download failed:", err);
      alert(`Plotly export failed: ${err.message}`);
    } finally {
      setDownloading(false);
    }
  };

  const handleCopy = useCallback(async () => {
    try {
      const text = bodyRef.current ? bodyRef.current.innerText : "";
      if (!text.trim()) return;
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("Copy failed:", err);
    }
  }, []);

  const plotlyLayout = figureJson
    ? {
        ...(figureJson.layout || {}),
        autosize: true,
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        font: { color: "#2c2c2c" },
      }
    : null;

  const renderBody = () => {
    if (figureJson) {
      return (
        <LazyPlot
          data={figureJson.data || []}
          layout={plotlyLayout}
          config={{
            responsive: true,
            displayModeBar: true,
            displaylogo: false,
          }}
          useResizeHandler
          style={{ width: "100%", height: "100%" }}
        />
      );
    }

    if (image) {
      return (
        <img
          src={imageUrl}
          alt={title || "Generated plot"}
          className="canvas-panel-image"
        />
      );
    }

    if (sandboxSessionId) {
      return <SandboxTerminal sessionId={sandboxSessionId} />;
    }

    if (effectiveContentType === "terminal" && chunks) {
      return (
        <div className="terminal-container">
          {chunks.map((chunk, i) => (
            <div
              key={i}
              className={
                chunk.startsWith("[stderr]")
                  ? "terminal-line stderr"
                  : "terminal-line"
              }
            >
              {chunk}
            </div>
          ))}
          <div className="terminal-cursor" />
        </div>
      );
    }

    // Image preview from /api/preview
    if (effectiveContentType === "image" && effectivePreviewUrl) {
      return (
        <img
          src={effectivePreviewUrl}
          alt={title || "Image preview"}
          className="canvas-panel-image"
        />
      );
    }

    // PDF preview via embed (more reliable than iframe for PDFs)
    if (effectiveContentType === "pdf" && effectivePreviewUrl) {
      if (iframeError) {
        return (
          <div
            className="canvas-preview-fallback"
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 12,
              padding: 24,
              textAlign: "center",
              color: "var(--text-secondary)",
            }}
          >
            <FileText size={32} />
            <span>Unable to preview PDF inline.</span>
            <button
              className="downloadable-btn"
              onClick={() => {
                fetch(effectivePreviewUrl)
                  .then((r) => r.blob())
                  .then((blob) => {
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = filename || "document.pdf";
                    a.style.display = "none";
                    document.body.appendChild(a);
                    a.click();
                    setTimeout(() => {
                      URL.revokeObjectURL(url);
                      if (a.parentNode) a.parentNode.removeChild(a);
                    }, 5000);
                  })
                  .catch((e) => console.error("PDF download failed:", e));
              }}
              style={{ marginTop: 8 }}
            >
              <Download size={13} /> Download PDF
            </button>
          </div>
        );
      }
      return (
        <embed
          src={effectivePreviewUrl}
          type="application/pdf"
          className="canvas-preview-iframe"
          onError={() => setIframeError(true)}
          style={{ width: "100%", height: "100%", border: "none" }}
        />
      );
    }

    if (effectiveContentType === "html" && effectiveContent) {
      return (
        <iframe
          srcDoc={effectiveContent}
          title={title || "HTML Preview"}
          className="canvas-preview-iframe"
          sandbox="allow-scripts"
        />
      );
    }

    if (effectiveContentType === "markdown" && effectiveContent) {
      return (
        <div className="canvas-preview-markdown">
          <ReactMarkdown>{effectiveContent}</ReactMarkdown>
        </div>
      );
    }

    if (effectiveContent) {
      // Pretty-print JSON when the content type signals it
      const isJson = (previewData?.content_type || "").includes("json");
      let displayed = effectiveContent;
      if (isJson) {
        try {
          displayed = JSON.stringify(JSON.parse(effectiveContent), null, 2);
        } catch {}
      }
      return <pre className="canvas-preview-pre">{displayed}</pre>;
    }

    return null;
  };

  const icon = sandboxSessionId ? (
    <Terminal size={15} />
  ) : effectiveContentType === "terminal" ? (
    <Terminal size={15} />
  ) : effectiveContentType === "image" ? (
    <ImageIcon size={15} />
  ) : effectiveContentType === "markdown" ||
    effectiveContentType === "html" ||
    effectiveContentType === "pdf" ||
    effectiveContentType === "text" ? (
    <FileText size={15} />
  ) : (
    <BarChart3 size={15} />
  );

  return (
    <div className="canvas-panel" style={style}>
      <div className="canvas-panel-header">
        <div className="canvas-panel-title">
          {icon}
          <span>{title || "Canvas"}</span>
        </div>
        <div className="canvas-panel-actions">
          <button
            className="canvas-panel-btn"
            onClick={handleCopy}
            title={copied ? "Copied!" : "Copy content"}
          >
            {copied ? <CheckIcon size={14} /> : <Copy size={14} />}
          </button>
          {!sandboxSessionId && (
            <button
              className="canvas-panel-btn"
              onClick={handleDownload}
              title={
                figureJson
                  ? "Export chart as PNG"
                  : filename
                    ? "Download file"
                    : "Download"
              }
              disabled={downloading}
            >
              <Download size={14} />
            </button>
          )}
          <button
            className="canvas-panel-btn canvas-panel-close"
            onClick={onClose}
            title="Close panel"
          >
            <X size={16} />
          </button>
        </div>
      </div>
      <div ref={bodyRef} className="canvas-panel-body">
        {renderBody()}
      </div>
      <div className="canvas-panel-footer">
        {image
          ? `Saved as ${image}`
          : filename
            ? filename
            : figureJson
              ? "Interactive Plotly chart"
              : sandboxSessionId
                ? `Sandbox session ${sandboxSessionId}`
                : contentType === "terminal"
                  ? "Live execution output"
                  : "Preview"}
      </div>
    </div>
  );
}
