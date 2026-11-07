import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
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
  Table as TableIcon,
  Code2,
  ExternalLink,
  Braces,
  Film,
  Music,
} from "lucide-react";
import LazyPlot from "./LazyPlot";
import ReactMarkdown from "react-markdown";
import CsvTable, { tableFromText } from "./CsvTable";
import { highlightCode } from "./codeHighlight";
import { fetchPreview, fileUrl } from "../hooks/api";

function normalizeContentType(mime) {
  if (!mime) return "text";
  const m = mime.toLowerCase();
  if (m.includes("markdown")) return "markdown";
  if (m.includes("html")) return "html";
  if (m.includes("svg")) return "svg";
  if (m.startsWith("image/")) return "image";
  if (m.startsWith("video/")) return "video";
  if (m.startsWith("audio/")) return "audio";
  if (m.includes("pdf")) return "pdf";
  if (m.includes("csv") || m.includes("tab-separated")) return "csv";
  if (m.includes("json")) return "json";
  if (m.startsWith("text/x-") || m.includes("javascript") || m.includes("typescript") || m.includes("yaml") || m.includes("xml")) return "code";
  return "text";
}

const EXT_TYPE = {
  csv: "csv", tsv: "csv", json: "json", svg: "svg", md: "markdown", html: "html", htm: "html",
  pdf: "pdf", png: "image", jpg: "image", jpeg: "image", gif: "image", webp: "image", bmp: "image",
  mp4: "video", webm: "video", mp3: "audio", wav: "audio",
  py: "code", js: "code", ts: "code", jsx: "code", tsx: "code", java: "code", c: "code", cpp: "code",
  h: "code", go: "code", rs: "code", sh: "code", sql: "code", yaml: "code", yml: "code", xml: "code", css: "code", r: "code",
};

function typeFromFilename(name) {
  const ext = (name || "").split(".").pop().toLowerCase();
  return EXT_TYPE[ext] || null;
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
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/sandbox/interact/${sessionId}`);
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
          setLines((prev) => [...prev, { kind: "system", text: `[process exited with code ${msg.code}]` }]);
          return;
        }
        if (msg.type === "artifacts") {
          setLines((prev) => [...prev, { kind: "system", text: `[produced: ${(msg.files || []).map((f) => f.name).join(", ")}]` }]);
          return;
        }
        if (msg.type === "stdout" || msg.type === "stderr" || msg.type === "status") {
          setLines((prev) => [...prev, { kind: msg.type, text: msg.data }]);
        }
      } catch {
        setLines((prev) => [...prev, { kind: "stdout", text: e.data }]);
      }
    };
    ws.onerror = () => setLines((prev) => [...prev, { kind: "stderr", text: "[connection error]" }]);
    ws.onclose = () => setReady(false);
    return () => {
      try { ws.close(); } catch {}
    };
  }, [sessionId]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
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
          <div key={i} className={`terminal-line ${l.kind === "stderr" ? "stderr" : ""} ${l.kind === "input" ? "input" : ""} ${l.kind === "system" || l.kind === "status" ? "system" : ""}`}>
            {l.text}
          </div>
        ))}
        {!exited && ready && <div className="terminal-cursor" />}
      </div>
      <form className="sandbox-terminal-input" onSubmit={(e) => { e.preventDefault(); handleSend(); }}>
        <span className="sandbox-terminal-prompt">stdin</span>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={exited ? "Session ended" : ready ? "Type input and press Enter…" : "Connecting…"}
          disabled={!ready || exited}
        />
        <button type="submit" disabled={!ready || exited || !input.trim()} title="Send">
          <Send size={13} />
        </button>
      </form>
    </div>
  );
}

function TerminalView({ chunks, artifacts, onOpenCanvas }) {
  const scrollRef = useRef(null);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [chunks]);
  return (
    <div className="terminal-view">
      <div className="terminal-container" ref={scrollRef}>
        {(chunks || []).map((chunk, i) => (
          <div
            key={i}
            className={
              chunk.startsWith("[stderr]")
                ? "terminal-line stderr"
                : chunk.startsWith("[status]")
                  ? "terminal-line system"
                  : "terminal-line"
            }
          >
            {chunk}
          </div>
        ))}
        <div className="terminal-cursor" />
      </div>
      {artifacts && artifacts.length > 0 && (
        <div className="terminal-artifacts">
          <span className="terminal-artifacts-label">Produced files</span>
          {artifacts.map((a) => (
            <button
              key={a.filename}
              className="terminal-artifact"
              onClick={() => onOpenCanvas && onOpenCanvas({ filename: a.filename, contentType: a.content_type, title: a.original_name || a.filename, previewUrl: a.preview_url })}
              title="Open in canvas"
            >
              <FileText size={12} /> {a.original_name || a.filename}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function CodeView({ content, filename, lang }) {
  const html = useMemo(() => highlightCode(content, lang || (filename || "").split(".").pop()), [content, filename, lang]);
  const lines = useMemo(() => (content || "").split("\n").length, [content]);
  return (
    <div className="canvas-code">
      <div className="canvas-code-gutter" aria-hidden="true">
        {Array.from({ length: lines }, (_, i) => (
          <span key={i}>{i + 1}</span>
        ))}
      </div>
      {html ? (
        <pre className="canvas-preview-pre hljs" dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <pre className="canvas-preview-pre">{content}</pre>
      )}
    </div>
  );
}

export default function CanvasPanel({
  image,
  figureJson,
  content,
  contentType,
  chunks,
  artifacts,
  filename,
  previewUrl,
  sandboxSessionId,
  csvPreview,
  lang,
  title,
  onClose,
  onOpenCanvas,
  style,
}) {
  const [iframeError, setIframeError] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [previewData, setPreviewData] = useState(null);
  const [previewError, setPreviewError] = useState(null);
  const [csvMode, setCsvMode] = useState("table");
  const bodyRef = useRef(null);

  useEffect(() => {
    setPreviewData(null);
    setPreviewError(null);
    setIframeError(false);
    if (filename && !content && !csvPreview) {
      fetchPreview(filename)
        .then((data) => setPreviewData(data))
        .catch((err) => setPreviewError(err.message));
    }
  }, [filename, content, csvPreview]);

  const effectiveContent = content || previewData?.content || "";
  const normalizedContentType = contentType
    ? contentType.includes("/")
      ? normalizeContentType(contentType)
      : contentType
    : null;
  const effectiveContentType =
    (csvPreview ? "csv" : null) ||
    normalizedContentType ||
    normalizeContentType(previewData?.content_type) ||
    (previewData?.kind === "pdf" ? "pdf" : null) ||
    (previewData?.kind === "image" ? "image" : null) ||
    (previewData?.kind === "video" ? "video" : null) ||
    (previewData?.kind === "audio" ? "audio" : null) ||
    typeFromFilename(filename) ||
    "text";
  const effectivePreviewUrl = previewUrl ? fileUrl(previewUrl) : previewData?.url ? fileUrl(previewData.url) : filename ? fileUrl(`/api/files/${encodeURIComponent(filename)}`) : null;

  const hasContent =
    image || figureJson || effectiveContent || (chunks && chunks.length > 0) || sandboxSessionId || effectivePreviewUrl || csvPreview || previewError;
  if (!hasContent) return null;

  const imageUrl = image ? fileUrl(`/api/plots/${encodeURIComponent(image)}`) : null;

  const triggerDownload = (href, name) => {
    fetch(href)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.blob();
      })
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = name;
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
        window.open(href, "_blank");
      });
  };

  const handleDownload = async () => {
    if (image) return triggerDownload(imageUrl, image);
    if (filename) return triggerDownload(fileUrl(`/api/download/${encodeURIComponent(filename)}`), filename);
    if (csvPreview) {
      const header = csvPreview.columns.map((c) => c.name).join(",");
      const body = csvPreview.rows.map((r) => r.map((v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v))).join(",")).join("\n");
      const blob = new Blob([header + "\n" + body], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = csvPreview.filename || "preview.csv";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      return;
    }
    if (effectiveContent && !figureJson) {
      const blob = new Blob([effectiveContent], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = title || "snippet.txt";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      return;
    }
    if (chunks && !figureJson) {
      const blob = new Blob([chunks.join("\n")], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "output.txt";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
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
      triggerDownload(fileUrl(`/api/plots/${encodeURIComponent(fname)}`), fname);
    } catch (err) {
      console.error("Plotly PNG download failed:", err);
      alert(`Plotly export failed: ${err.message}`);
    } finally {
      setDownloading(false);
    }
  };

  const handleCopy = useCallback(async () => {
    try {
      const text = effectiveContent || (chunks ? chunks.join("\n") : bodyRef.current ? bodyRef.current.innerText : "");
      if (!text.trim()) return;
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("Copy failed:", err);
    }
  }, [effectiveContent, chunks]);

  const plotlyLayout = figureJson
    ? { ...(figureJson.layout || {}), autosize: true, paper_bgcolor: "transparent", plot_bgcolor: "transparent", font: { color: "#2c2c2c" } }
    : null;

  const csvTable = useMemo(() => {
    if (csvPreview) return { columns: csvPreview.columns, rows: csvPreview.rows };
    if (effectiveContentType === "csv" && effectiveContent) return tableFromText(effectiveContent);
    return null;
  }, [csvPreview, effectiveContentType, effectiveContent]);

  const renderBody = () => {
    if (previewError) {
      return <div className="canvas-preview-fallback">Could not load preview: {previewError}</div>;
    }
    if (figureJson) {
      return (
        <LazyPlot
          data={figureJson.data || []}
          layout={plotlyLayout}
          config={{ responsive: true, displayModeBar: true, displaylogo: false }}
          useResizeHandler
          style={{ width: "100%", height: "100%" }}
        />
      );
    }
    if (image) return <img src={imageUrl} alt={title || "Generated plot"} className="canvas-panel-image" />;
    if (sandboxSessionId) return <SandboxTerminal sessionId={sandboxSessionId} />;
    if (effectiveContentType === "terminal" && chunks) return <TerminalView chunks={chunks} artifacts={artifacts} onOpenCanvas={onOpenCanvas} />;

    if (effectiveContentType === "csv" && csvTable) {
      if (csvMode === "raw" && effectiveContent) return <pre className="canvas-preview-pre">{effectiveContent}</pre>;
      return (
        <CsvTable
          columns={csvTable.columns}
          rows={csvTable.rows}
          footer={csvPreview && csvPreview.total_rows > csvPreview.returned_rows ? `showing ${csvPreview.returned_rows} of ${csvPreview.filtered_rows.toLocaleString()}` : previewData?.truncated ? "file truncated for preview" : null}
        />
      );
    }

    if (effectiveContentType === "image" && effectivePreviewUrl) {
      return <img src={effectivePreviewUrl} alt={title || "Image preview"} className="canvas-panel-image" />;
    }
    if (effectiveContentType === "video" && effectivePreviewUrl) {
      return <video src={effectivePreviewUrl} controls className="canvas-panel-media" />;
    }
    if (effectiveContentType === "audio" && effectivePreviewUrl) {
      return <audio src={effectivePreviewUrl} controls className="canvas-panel-media" />;
    }

    if (effectiveContentType === "pdf" && effectivePreviewUrl) {
      if (iframeError) {
        return (
          <div className="canvas-preview-fallback">
            <FileText size={32} />
            <span>Unable to preview PDF inline.</span>
            <a className="downloadable-btn" href={effectivePreviewUrl} target="_blank" rel="noreferrer">
              <ExternalLink size={13} /> Open in new tab
            </a>
            <button className="downloadable-btn" onClick={() => triggerDownload(effectivePreviewUrl, filename || "document.pdf")}>
              <Download size={13} /> Download PDF
            </button>
          </div>
        );
      }
      return (
        <iframe
          src={`${effectivePreviewUrl}#toolbar=1&view=FitH`}
          title={title || "PDF preview"}
          className="canvas-preview-iframe"
          onError={() => setIframeError(true)}
        />
      );
    }

    if (effectiveContentType === "svg") {
      if (effectiveContent) {
        return <div className="canvas-svg" dangerouslySetInnerHTML={{ __html: effectiveContent }} />;
      }
      if (effectivePreviewUrl) return <img src={effectivePreviewUrl} alt={title || "SVG"} className="canvas-panel-image" />;
    }

    if (effectiveContentType === "html" && effectiveContent) {
      return (
        <iframe
          srcDoc={effectiveContent}
          title={title || "HTML Preview"}
          className="canvas-preview-iframe"
          sandbox="allow-scripts allow-forms allow-modals allow-popups"
        />
      );
    }
    if (effectiveContentType === "html" && effectivePreviewUrl) {
      return <iframe src={effectivePreviewUrl} title={title || "HTML Preview"} className="canvas-preview-iframe" sandbox="allow-scripts allow-forms allow-modals allow-popups" />;
    }

    if (effectiveContentType === "markdown" && effectiveContent) {
      return (
        <div className="canvas-preview-markdown">
          <ReactMarkdown>{effectiveContent}</ReactMarkdown>
        </div>
      );
    }

    if (effectiveContentType === "json" && effectiveContent) {
      let displayed = effectiveContent;
      try {
        displayed = JSON.stringify(JSON.parse(effectiveContent), null, 2);
      } catch {}
      return <CodeView content={displayed} lang="json" />;
    }

    if (effectiveContentType === "code" && effectiveContent) {
      return <CodeView content={effectiveContent} filename={filename || title} lang={lang} />;
    }

    if (effectiveContent) {
      return <pre className="canvas-preview-pre">{effectiveContent}</pre>;
    }
    if (effectivePreviewUrl) {
      return (
        <div className="canvas-preview-fallback">
          <FileText size={32} />
          <span>No inline preview for this file type.</span>
          <button className="downloadable-btn" onClick={() => triggerDownload(fileUrl(`/api/download/${encodeURIComponent(filename)}`), filename)}>
            <Download size={13} /> Download
          </button>
        </div>
      );
    }
    return null;
  };

  const icon = sandboxSessionId || effectiveContentType === "terminal" ? (
    <Terminal size={15} />
  ) : effectiveContentType === "image" || effectiveContentType === "svg" ? (
    <ImageIcon size={15} />
  ) : effectiveContentType === "csv" ? (
    <TableIcon size={15} />
  ) : effectiveContentType === "code" ? (
    <Code2 size={15} />
  ) : effectiveContentType === "json" ? (
    <Braces size={15} />
  ) : effectiveContentType === "video" ? (
    <Film size={15} />
  ) : effectiveContentType === "audio" ? (
    <Music size={15} />
  ) : figureJson || image ? (
    <BarChart3 size={15} />
  ) : (
    <FileText size={15} />
  );

  return (
    <div className="canvas-panel" style={style}>
      <div className="canvas-panel-header">
        <div className="canvas-panel-title">
          {icon}
          <span>{title || "Canvas"}</span>
        </div>
        <div className="canvas-panel-actions">
          {effectiveContentType === "csv" && effectiveContent && (
            <button className="canvas-panel-btn" onClick={() => setCsvMode(csvMode === "table" ? "raw" : "table")} title={csvMode === "table" ? "Show raw text" : "Show table"}>
              {csvMode === "table" ? <Code2 size={14} /> : <TableIcon size={14} />}
            </button>
          )}
          {effectivePreviewUrl && !sandboxSessionId && (
            <a className="canvas-panel-btn" href={effectivePreviewUrl} target="_blank" rel="noreferrer" title="Open in new tab">
              <ExternalLink size={14} />
            </a>
          )}
          <button className="canvas-panel-btn" onClick={handleCopy} title={copied ? "Copied!" : "Copy content"}>
            {copied ? <CheckIcon size={14} /> : <Copy size={14} />}
          </button>
          {!sandboxSessionId && (
            <button className="canvas-panel-btn" onClick={handleDownload} title={figureJson ? "Export chart as PNG" : "Download"} disabled={downloading}>
              <Download size={14} />
            </button>
          )}
          <button className="canvas-panel-btn canvas-panel-close" onClick={onClose} title="Close panel">
            <X size={16} />
          </button>
        </div>
      </div>
      <div ref={bodyRef} className={`canvas-panel-body ${effectiveContentType === "csv" ? "no-pad" : ""}`}>
        {renderBody()}
      </div>
      <div className="canvas-panel-footer">
        {image
          ? `Saved as ${image}`
          : filename
            ? `${filename}${previewData?.size ? ` · ${(previewData.size / 1024).toFixed(1)} KB` : ""}`
            : csvPreview
              ? `${csvPreview.filename} · ${csvPreview.total_rows.toLocaleString()} rows`
              : figureJson
                ? "Interactive Plotly chart"
                : sandboxSessionId
                  ? `Sandbox session ${sandboxSessionId}`
                  : effectiveContentType === "terminal"
                    ? "Live execution output"
                    : "Preview"}
      </div>
    </div>
  );
}
