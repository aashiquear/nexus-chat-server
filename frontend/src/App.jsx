import React, {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
} from "react";
import { Menu, MessageCircle, Wifi, WifiOff, Scissors, BookOpen, X, Loader2, Plus } from "lucide-react";
import Sidebar, { ModelBadges } from "./components/Sidebar";
import ChatMessage, { TypingIndicator } from "./components/ChatMessage";
import ChatInput from "./components/ChatInput";
import CanvasPanel from "./components/CanvasPanel";
import SplashScreen from "./components/SplashScreen";
import MCPMarketplacePanel from "./components/MCPMarketplacePanel";
import { useChat } from "./hooks/useChat";
import {
  fetchModels,
  fetchTools,
  fetchToolsWithPolicy,
  fetchFiles,
  uploadFile,
  deleteFile,
  fetchUploadProgress,
  fetchMCPServers,
  reconnectMCPServer,
  removeMCPServer,
  fetchMCPResources,
  ingestMCPResource,
  fetchConversations,
  fetchConversation,
  saveConversation,
  deleteConversation,
  fetchGenerationSettings,
  runCode,
} from "./hooks/api";

// ---------------------------------------------------------------------
// Generation settings persistence (per browser). The server edition
// swaps these two helpers for per-user storage.
// ---------------------------------------------------------------------
const GEN_STORAGE_KEY = "nexus:generation";

export function loadStoredGeneration() {
  try {
    return JSON.parse(localStorage.getItem(GEN_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

export function storeGeneration(value) {
  try {
    localStorage.setItem(GEN_STORAGE_KEY, JSON.stringify(value));
  } catch {}
}

// Small modal listing an MCP server's resources with an "add to RAG" action.
function MCPResourcesDialog({ server, onClose, onIngested }) {
  const [resources, setResources] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    fetchMCPResources(server.id).then(setResources).catch((e) => setError(e.message));
  }, [server.id]);
  const ingest = async (uri) => {
    setBusy(uri);
    setError("");
    try {
      await ingestMCPResource(server.id, uri);
      onIngested && (await onIngested());
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="mcp-modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="mcp-modal" style={{ width: 520 }}>
        <div className="mcp-modal-header">
          <h3>
            <BookOpen size={16} /> {server.name} — resources
          </h3>
          <button className="gen-icon-btn" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <div className="mcp-modal-body">
          <p className="mcp-help">Resources exposed by this MCP server can be indexed into the RAG store and then selected like any uploaded file.</p>
          {error && <div className="mcp-error">{error}</div>}
          {resources === null && <Loader2 size={16} className="spin" />}
          {resources && resources.length === 0 && <div className="mcp-result-desc">This server exposes no resources.</div>}
          {resources &&
            resources.map((r) => (
              <div key={r.uri} className="mcp-import-row">
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="mcp-import-id">{r.name || r.uri}</div>
                  <div className="mcp-result-desc" style={{ wordBreak: "break-all" }}>
                    {r.uri}
                    {r.mimeType ? ` · ${r.mimeType}` : ""}
                  </div>
                </div>
                <button className="gen-btn" onClick={() => ingest(r.uri)} disabled={busy === r.uri}>
                  {busy === r.uri ? <Loader2 size={13} className="spin" /> : <Plus size={13} />} Add to RAG
                </button>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}

export default function App() {
  // Splash / title screen - dismissed after user clicks "Start Chat"
  const [showSplash, setShowSplash] = useState(() => {
    try {
      return sessionStorage.getItem("nexus:splashDismissed") !== "1";
    } catch {
      return true;
    }
  });

  const dismissSplash = useCallback(() => {
    try {
      sessionStorage.setItem("nexus:splashDismissed", "1");
    } catch {}
    setShowSplash(false);
  }, []);

  // State
  const [models, setModels] = useState([]);
  const [tools, setTools] = useState([]);
  const [files, setFiles] = useState([]);
  const [mcpServers, setMcpServers] = useState([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [selectedTools, setSelectedTools] = useState([]);
  // Tool selection mode: "auto" (discovery router + pins) or "manual".
  const [toolMode, setToolMode] = useState(() => {
    try {
      return localStorage.getItem("nexus:toolMode") || "";
    } catch {
      return "";
    }
  });
  const [lastToolSelection, setLastToolSelection] = useState(null);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [messages, setMessages] = useState([]);
  const [inputValue, setInputValue] = useState("");
  const [pendingImages, setPendingImages] = useState([]);
  const [isTyping, setIsTyping] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showMarketplace, setShowMarketplace] = useState(false);
  const [resourcesServer, setResourcesServer] = useState(null);

  // LLM response status — drives the floating pop-up above the chat input.
  const [llmStatus, setLlmStatus] = useState({ stage: "idle" });
  const [notice, setNotice] = useState(null);

  // Generation settings: server defaults + per-kind user overrides.
  const [genDefaults, setGenDefaults] = useState(null); // {defaults:{local,cloud}, context_sizes}
  const [genOverrides, setGenOverrides] = useState(() => loadStoredGeneration());
  const [contextInfo, setContextInfo] = useState(null); // {budget, estimated_prompt_tokens, lastPromptTokens}
  const [compactionState, setCompactionState] = useState(null);
  const compactionRef = useRef(null);

  // Conversation state
  const [conversations, setConversations] = useState([]);
  const [activeConversationId, setActiveConversationId] = useState(null);

  // Canvas panel state (right-side panel for graphs)
  const [canvasData, setCanvasData] = useState(null);
  const [canvasWidth, setCanvasWidth] = useState(null); // null = auto-calculate initial
  const isDraggingRef = useRef(false);

  // Upload progress state: { filename, stage, percent (0-100) } or null
  const [uploadProgress, setUploadProgress] = useState(null);

  // Token usage tracking
  const [tokenUsageHistory, setTokenUsageHistory] = useState([]);
  const tokenUsageRef = useRef([]);
  const [lastResponseStats, setLastResponseStats] = useState(null);

  const chatEndRef = useRef(null);
  const chatAreaRef = useRef(null);
  const streamBufferRef = useRef("");
  const responseStartRef = useRef(null);
  const userScrolledRef = useRef(false);
  const { connect, sendMessage, isConnected, isStreaming } = useChat();

  // Initialize
  useEffect(() => {
    connect();
    loadData();
  }, [connect]);

  const refreshModels = useCallback(async () => {
    const m = await fetchModels();
    setModels(m);
    return m;
  }, []);

  const loadData = async () => {
    try {
      const [m, tp, f, mcp, convos, gen] = await Promise.all([
        fetchModels(),
        fetchToolsWithPolicy(),
        fetchFiles(),
        fetchMCPServers(),
        fetchConversations(),
        fetchGenerationSettings(),
      ]);
      const t = tp.tools;
      setModels(m);
      setTools(t);
      setToolMode((prev) => prev || tp.selection?.mode || "auto");
      setFiles(f);
      setMcpServers(mcp);
      setConversations(convos);
      if (gen) setGenDefaults(gen);

      // Default-select a model that is both provider-configured *and*
      // confirmed on the remote (when probe info is available).
      const isUsable = (x) =>
        x.available &&
        (x.remote_available === null ||
          x.remote_available === undefined ||
          x.remote_available);
      const preferred = m.find(isUsable) || m.find((x) => x.available);
      if (preferred) setSelectedModel((prev) => prev || preferred.id);

      // Manual mode starts with every built-in tool ticked; auto mode
      // starts with no pins (the router picks per message).
      const mode = tp.selection?.mode || "auto";
      setSelectedTools((prev) =>
        prev.length || mode === "auto" ? prev : t.filter((x) => x.source !== "mcp").map((x) => x.id),
      );
    } catch (err) {
      console.error("Failed to load data:", err);
    }
  };

  const currentModel = useMemo(
    () => models.find((m) => m.id === selectedModel) || null,
    [models, selectedModel],
  );
  const modelKind = currentModel?.kind === "local" ? "local" : "cloud";
  const visionCapable = currentModel
    ? currentModel.capabilities
      ? !!currentModel.capabilities.vision
      : null
    : null;

  // Effective generation settings for the current model kind.
  const generation = useMemo(() => {
    const base = genDefaults?.defaults?.[modelKind] || {};
    const override = genOverrides[modelKind] || {};
    return {
      ...base,
      ...override,
      compaction: { ...(base.compaction || {}), ...(override.compaction || {}) },
    };
  }, [genDefaults, genOverrides, modelKind]);

  const updateGeneration = useCallback(
    (partial) => {
      setGenOverrides((prev) => {
        const cur = prev[modelKind] || {};
        const next = { ...cur, ...partial };
        if (partial.compaction) next.compaction = { ...(cur.compaction || {}), ...partial.compaction };
        const all = { ...prev, [modelKind]: next };
        storeGeneration(all);
        return all;
      });
    },
    [modelKind],
  );

  const resetGeneration = useCallback(() => {
    setGenOverrides((prev) => {
      const all = { ...prev };
      delete all[modelKind];
      storeGeneration(all);
      return all;
    });
  }, [modelKind]);

  // "Save as default" freezes the current effective values as the
  // override set for this kind (so later server-default changes don't
  // silently move them).
  const saveGenerationDefault = useCallback(() => {
    setGenOverrides((prev) => {
      const all = { ...prev, [modelKind]: generation };
      storeGeneration(all);
      return all;
    });
    setNotice(`Saved as default for ${modelKind} models`);
    setTimeout(() => setNotice(null), 2500);
  }, [generation, modelKind]);

  // Smart auto-scroll: only auto-scroll if user is near the bottom
  const handleScroll = useCallback(() => {
    const el = chatAreaRef.current;
    if (!el) return;
    const threshold = 100;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
    userScrolledRef.current = !atBottom;
  }, []);

  useEffect(() => {
    const el = chatAreaRef.current;
    if (!el) return;
    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => el.removeEventListener("scroll", handleScroll);
  }, [handleScroll]);

  useEffect(() => {
    if (!userScrolledRef.current) {
      chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, isTyping]);

  const handleToolModeChange = useCallback(
    (mode) => {
      setToolMode(mode);
      try {
        localStorage.setItem("nexus:toolMode", mode);
      } catch {}
      if (mode === "manual" && selectedTools.length === 0) {
        setSelectedTools(tools.filter((x) => x.source !== "mcp").map((x) => x.id));
      }
    },
    [selectedTools.length, tools],
  );

  // Toggle helpers
  const toggleTool = (id) => {
    const tool = tools.find((t) => t.id === id);
    const childIds = tool?.children || [];
    setSelectedTools((prev) => {
      if (prev.includes(id)) {
        const toRemove = new Set([id, ...childIds]);
        return prev.filter((x) => !toRemove.has(x));
      } else {
        const toAdd = [id, ...childIds].filter((x) => !prev.includes(x));
        return [...prev, ...toAdd];
      }
    });
  };

  const toggleFile = (name) => {
    setSelectedFiles((prev) =>
      prev.includes(name) ? prev.filter((x) => x !== name) : [...prev, name],
    );
  };

  const removeFile = (name) => {
    setSelectedFiles((prev) => prev.filter((x) => x !== name));
  };

  // Upload
  const handleUpload = async (file) => {
    try {
      setUploadProgress({ filename: file.name, stage: "uploading", percent: 0 });
      await uploadFile(file, (percent) => {
        setUploadProgress({ filename: file.name, stage: "uploading", percent });
      });

      setUploadProgress({ filename: file.name, stage: "embedding", percent: 0 });

      let done = false;
      let attempts = 0;
      while (!done && attempts < 600) {
        attempts += 1;
        const progress = await fetchUploadProgress(file.name);
        if (!progress || progress.stage === "unknown") {
          await new Promise((r) => setTimeout(r, 250));
          continue;
        }
        const stage = progress.stage === "complete" ? "embedding" : progress.stage;
        setUploadProgress({
          filename: file.name,
          stage: stage === "reading" || stage === "chunking" || stage === "queued" ? "embedding" : stage,
          percent: progress.percent || 0,
          subStage: progress.stage,
        });
        if (progress.stage === "complete" || progress.stage === "error") {
          done = true;
          break;
        }
        await new Promise((r) => setTimeout(r, 350));
      }

      setUploadProgress(null);
      const updatedFiles = await fetchFiles();
      setFiles(updatedFiles);
      if (!selectedFiles.includes(file.name)) {
        setSelectedFiles((prev) => [...prev, file.name]);
      }
    } catch (err) {
      setUploadProgress(null);
      alert(`Upload failed: ${err.message}`);
    }
  };

  const refreshToolsAndMcp = async () => {
    const [t, mcp] = await Promise.all([fetchTools(), fetchMCPServers()]);
    setTools(t);
    setMcpServers(mcp);
  };

  const handleReconnectMCP = async (serverId) => {
    try {
      await reconnectMCPServer(serverId);
    } catch (err) {
      console.error("MCP reconnect failed:", err);
      setNotice(err.message);
      setTimeout(() => setNotice(null), 4000);
    }
    await refreshToolsAndMcp();
  };

  const handleRemoveMCP = async (serverId) => {
    if (!window.confirm("Remove this MCP server?")) return;
    try {
      await removeMCPServer(serverId);
      setSelectedTools((prev) => prev.filter((id) => !id.startsWith(`mcp:${serverId}:`)));
    } catch (err) {
      alert(`Failed to remove MCP server: ${err.message}`);
    }
    await refreshToolsAndMcp();
  };

  const handleDeleteFile = async (filename) => {
    try {
      await deleteFile(filename);
      setFiles((prev) => prev.filter((f) => f.name !== filename));
      setSelectedFiles((prev) => prev.filter((n) => n !== filename));
    } catch (err) {
      console.error("Delete failed:", err);
    }
  };

  // ---- Conversation persistence ----

  const persistConversation = useCallback(
    async (msgs, convId, tokenUsage) => {
      if (!msgs || msgs.length === 0) return null;
      try {
        const result = await saveConversation({
          id: convId || undefined,
          messages: msgs,
          model: selectedModel,
          token_usage: tokenUsage || undefined,
          compaction_state: compactionRef.current || undefined,
          selected_tools: selectedTools,
          selected_files: selectedFiles,
        });
        const convos = await fetchConversations();
        setConversations(convos);
        return result.id;
      } catch (err) {
        console.error("Save conversation failed:", err);
        return convId;
      }
    },
    [selectedModel, selectedTools, selectedFiles],
  );

  const handleSelectConversation = async (id) => {
    try {
      const data = await fetchConversation(id);
      if (data) {
        setMessages(data.messages || []);
        setActiveConversationId(data.id);
        if (data.model && models.some((m) => m.id === data.model)) setSelectedModel(data.model);
        if (Array.isArray(data.selected_tools) && data.selected_tools.length) setSelectedTools(data.selected_tools);
        if (Array.isArray(data.selected_files)) setSelectedFiles(data.selected_files);
        setSidebarOpen(false);
        setTokenUsageHistory(data.token_usage || []);
        tokenUsageRef.current = data.token_usage || [];
        setLastResponseStats(null);
        setCompactionState(data.compaction_state || null);
        compactionRef.current = data.compaction_state || null;
        setContextInfo(null);
      }
    } catch (err) {
      console.error("Failed to load conversation:", err);
    }
  };

  const handleDeleteConversation = async (id) => {
    try {
      await deleteConversation(id);
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (activeConversationId === id) {
        setMessages([]);
        setActiveConversationId(null);
      }
    } catch (err) {
      console.error("Delete conversation failed:", err);
    }
  };

  // Open canvas panel
  const handleOpenCanvas = useCallback((data) => {
    setCanvasData(data);
    setCanvasWidth(null);
  }, []);

  const handlePreviewFile = useCallback(
    (file) => {
      handleOpenCanvas({ filename: file.name, title: file.name });
    },
    [handleOpenCanvas],
  );

  // Run a code block directly in the sandbox (Run button).
  const handleRunCode = useCallback(
    async ({ code, language }) => {
      const title = `Run (${language})`;
      setCanvasData({ contentType: "terminal", title, chunks: [`[status]: running ${language}…`], artifacts: [] });
      setCanvasWidth(null);
      try {
        const result = await runCode({ code, language }, (line) => {
          setCanvasData((prev) =>
            prev && prev.contentType === "terminal" && prev.title === title
              ? { ...prev, chunks: [...prev.chunks.filter((c) => !c.startsWith("[status]: running")), line] }
              : prev,
          );
        });
        if (result) {
          setCanvasData((prev) => {
            if (!prev || prev.title !== title) return prev;
            const chunks = result.output ? result.output.split("\n") : prev.chunks;
            return { ...prev, chunks: [...chunks, `[status]: exit ${result.return_code}`], artifacts: result.artifacts || [] };
          });
          // HTML / single artifact → jump straight to its preview.
          if (result.downloadable) {
            setCanvasData({ filename: result.downloadable.filename, contentType: result.downloadable.content_type, previewUrl: result.downloadable.preview_url, title: result.downloadable.original_name || result.downloadable.filename });
          }
        }
      } catch (err) {
        setCanvasData((prev) => (prev && prev.title === title ? { ...prev, chunks: [...prev.chunks, `[stderr]: ${err.message}`] } : prev));
      }
    },
    [],
  );

  // Canvas divider drag-to-resize
  const resizeCanvas = useCallback((delta) => {
    setCanvasWidth((prev) => {
      const current = prev || window.innerWidth * 0.4;
      const minWidth = 300;
      const maxWidth = window.innerWidth * 0.7;
      return Math.max(minWidth, Math.min(maxWidth, current + delta));
    });
  }, []);

  const handleDividerMouseDown = useCallback((e) => {
    e.preventDefault();
    isDraggingRef.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const onMouseMove = (e) => {
      if (!isDraggingRef.current) return;
      const newWidth = window.innerWidth - e.clientX;
      const minWidth = 300;
      const maxWidth = window.innerWidth * 0.7;
      setCanvasWidth(Math.max(minWidth, Math.min(maxWidth, newWidth)));
    };

    const onMouseUp = () => {
      isDraggingRef.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  }, []);

  const handleDividerKeyDown = useCallback(
    (e) => {
      const step = e.shiftKey ? 50 : 10;
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        resizeCanvas(step);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        resizeCanvas(-step);
      }
    },
    [resizeCanvas],
  );

  // Compute conversation statistics from messages and token history
  const conversationStats = useMemo(() => {
    const countWords = (text) => (text ? text.trim().split(/\s+/).filter(Boolean).length : 0);
    const userMsgs = messages.filter((m) => m.role === "user");
    const assistantMsgs = messages.filter((m) => m.role === "assistant");
    const totalUserWords = userMsgs.reduce((sum, m) => sum + countWords(m.content), 0);
    const assistantWordCounts = assistantMsgs.map((m) => countWords(m.content));
    const totalAssistantWords = assistantWordCounts.reduce((a, b) => a + b, 0);
    const totalPromptTokens = tokenUsageHistory.reduce((sum, u) => sum + (u.prompt_tokens || 0), 0);
    const totalCompletionTokens = tokenUsageHistory.reduce((sum, u) => sum + (u.completion_tokens || 0), 0);
    const totalTokens = tokenUsageHistory.reduce((sum, u) => sum + (u.total_tokens || 0), 0);
    const completionTokenCounts = tokenUsageHistory.map((u) => u.completion_tokens || 0);
    return {
      userMessages: userMsgs.length,
      assistantMessages: assistantMsgs.length,
      totalUserWords,
      totalAssistantWords,
      totalPromptTokens,
      totalCompletionTokens,
      totalTokens,
      maxResponseWords: assistantWordCounts.length ? Math.max(...assistantWordCounts) : 0,
      maxResponseTokens: completionTokenCounts.length ? Math.max(...completionTokenCounts) : 0,
      avgResponseWords: assistantMsgs.length ? Math.round(totalAssistantWords / assistantMsgs.length) : 0,
      avgResponseTokens: tokenUsageHistory.length ? Math.round(totalCompletionTokens / tokenUsageHistory.length) : 0,
    };
  }, [messages, tokenUsageHistory]);

  // Send message
  const handleSend = useCallback(() => {
    const text = inputValue.trim();
    if ((!text && pendingImages.length === 0) || !selectedModel) return;

    const userMsg = { role: "user", content: text || "(see attached image)" };
    if (pendingImages.length) userMsg.images = pendingImages.map((i) => i.dataUrl);
    const newMessages = [...messages, userMsg];
    setMessages(newMessages);
    setInputValue("");
    setPendingImages([]);
    setIsTyping(true);
    setNotice(null);
    setLlmStatus({ stage: "initiated" });
    streamBufferRef.current = "";
    responseStartRef.current = Date.now();
    userScrolledRef.current = false;

    // Build message history for the API (images ride along for vision models)
    const apiMessages = newMessages.map((m) => {
      const out = { role: m.role, content: m.content };
      if (m.images && m.images.length) out.images = m.images.map((img) => (typeof img === "string" ? img : img.dataUrl));
      return out;
    });

    const toolCalls = [];
    const toolResults = [];
    const notices = [];
    let currentConvId = activeConversationId;

    const upsertAssistant = (patch) => {
      setMessages((prev) => {
        const updated = [...prev];
        const lastIdx = updated.length - 1;
        if (lastIdx >= 0 && updated[lastIdx].role === "assistant") {
          updated[lastIdx] = { ...updated[lastIdx], ...patch(updated[lastIdx]) };
        } else {
          updated.push({ role: "assistant", content: "", toolCalls: [], toolResults: [], startedAt: responseStartRef.current, ...patch({}) });
        }
        return updated;
      });
    };

    sendMessage(
      {
        messages: apiMessages,
        model: selectedModel,
        tools: selectedTools,
        files: selectedFiles,
        generation,
        compaction_state: compactionRef.current || undefined,
        tool_mode: toolMode || undefined,
      },
      async (event) => {
        switch (event.type) {
          case "tools_selected":
            setLastToolSelection(event);
            upsertAssistant((m) => ({
              toolSelection: event.mode === "discover" && m.toolSelection
                ? { ...m.toolSelection, matched: [...(m.toolSelection.matched || []), ...(event.matched || [])], discovered: true }
                : event,
            }));
            break;

          case "status":
            if (event.stage) setLlmStatus({ stage: event.stage });
            break;

          case "notice":
            notices.push(event.content);
            setNotice(event.content);
            upsertAssistant(() => ({ notices: [...notices] }));
            break;

          case "compaction":
            compactionRef.current = event.state || null;
            setCompactionState(event.state || null);
            {
              const msg = `Context compacted (${event.mode}): ${event.removed_messages} older message(s) ≈ ${event.removed_tokens} tokens folded away`;
              notices.push(msg);
              upsertAssistant(() => ({ notices: [...notices] }));
            }
            break;

          case "context":
            setContextInfo((prev) => ({ ...(prev || {}), budget: event.budget, estimated_prompt_tokens: event.estimated_prompt_tokens, generation: event.generation }));
            break;

          case "text":
            streamBufferRef.current += event.content;
            upsertAssistant((m) => ({
              content: streamBufferRef.current,
              toolCalls: [...toolCalls],
              toolResults: [...toolResults],
              startedAt: m.startedAt ?? responseStartRef.current,
            }));
            setIsTyping(false);
            {
              const buf = streamBufferRef.current;
              const lastOpen = Math.max(buf.lastIndexOf("<think>"), buf.lastIndexOf("<thinking>"));
              const lastClose = Math.max(buf.lastIndexOf("</think>"), buf.lastIndexOf("</thinking>"));
              setLlmStatus({ stage: lastOpen > lastClose ? "thinking" : "responding" });
            }
            break;

          case "tool_call":
            setLlmStatus({ stage: "tool_calling", detail: event.name });
            toolCalls.push({ name: event.name, arguments: event.arguments });
            upsertAssistant((m) => ({ toolCalls: [...toolCalls], startedAt: m.startedAt ?? responseStartRef.current }));
            break;

          case "tool_stream":
            setCanvasData((prev) => {
              if (prev && prev.type === "terminal" && prev.name === event.name) {
                return { ...prev, chunks: [...prev.chunks, event.chunk] };
              }
              return {
                type: "terminal",
                name: event.name,
                title: `Running ${event.name}…`,
                chunks: [event.chunk],
                contentType: "terminal",
              };
            });
            break;

          case "tool_result":
            setLlmStatus({ stage: "tool_executing", detail: event.name });
            toolResults.push({ name: event.name, result: event.result });
            upsertAssistant(() => ({ toolResults: [...toolResults] }));
            try {
              const parsed = JSON.parse(event.result);
              if (parsed && parsed.figure_json) {
                const figData = typeof parsed.figure_json === "string" ? JSON.parse(parsed.figure_json) : parsed.figure_json;
                setCanvasData({ figureJson: figData, title: parsed.title || figData.layout?.title?.text || "Interactive Plot" });
              } else if (parsed && parsed.plot_image) {
                setCanvasData({ image: parsed.plot_image, title: parsed.title || "Generated Plot" });
              } else if (parsed && parsed.csv_preview) {
                setCanvasData({ csvPreview: parsed.csv_preview, title: parsed.title || parsed.csv_preview.filename });
              } else if (parsed && parsed.interactive_session) {
                setCanvasData({ sandboxSessionId: parsed.interactive_session, title: "Sandbox Console", contentType: "terminal", chunks: [] });
              } else if (event.name === "code_executor" && parsed && parsed.output != null) {
                if (parsed.downloadable && parsed.language === "html") {
                  setCanvasData({ filename: parsed.downloadable.filename, contentType: parsed.downloadable.content_type, previewUrl: parsed.downloadable.preview_url, title: parsed.downloadable.original_name || "HTML preview" });
                } else {
                  setCanvasData({ title: `Code Output${parsed.language ? ` (${parsed.language})` : ""}`, contentType: "terminal", chunks: parsed.output.split("\n"), artifacts: parsed.artifacts || [] });
                }
              }
            } catch (_e) {
              /* not JSON */
            }
            break;

          case "done":
            setIsTyping(false);
            setLlmStatus({ stage: "idle" });
            {
              const start = responseStartRef.current;
              if (start) {
                const finalMs = Date.now() - start;
                upsertAssistant((m) => ({ durationMs: finalMs, startedAt: m.startedAt ?? start }));
              }
              responseStartRef.current = null;
            }
            if (event.usage) {
              setTokenUsageHistory((prev) => {
                const updated = [...prev, event.usage];
                tokenUsageRef.current = updated;
                return updated;
              });
              setContextInfo((prev) => ({ ...(prev || {}), lastPromptTokens: event.usage.prompt_tokens || 0 }));
            }
            {
              const lastWords = streamBufferRef.current ? streamBufferRef.current.trim().split(/\s+/).filter(Boolean).length : 0;
              setLastResponseStats({
                words: lastWords,
                tokens: event.usage ? event.usage.completion_tokens || 0 : 0,
                promptTokens: event.usage ? event.usage.prompt_tokens || 0 : 0,
              });
            }
            setMessages((prev) => {
              setTimeout(async () => {
                const savedId = await persistConversation(prev, currentConvId, tokenUsageRef.current);
                if (savedId && !currentConvId) {
                  setActiveConversationId(savedId);
                  currentConvId = savedId;
                }
              }, 100);
              return prev;
            });
            break;

          case "error":
            setIsTyping(false);
            setLlmStatus({ stage: "error", detail: event.content });
            responseStartRef.current = null;
            setMessages((prev) => [...prev, { role: "assistant", content: `⚠️ ${event.content}` }]);
            break;
        }
      },
    );
  }, [inputValue, pendingImages, messages, selectedModel, selectedTools, selectedFiles, sendMessage, activeConversationId, persistConversation, generation, toolMode]);

  // New chat
  const handleNewChat = () => {
    setMessages([]);
    setActiveConversationId(null);
    setInputValue("");
    setPendingImages([]);
    setSidebarOpen(false);
    setCanvasData(null);
    setTokenUsageHistory([]);
    tokenUsageRef.current = [];
    setLastResponseStats(null);
    setLlmStatus({ stage: "idle" });
    setCompactionState(null);
    compactionRef.current = null;
    setContextInfo(null);
    setNotice(null);
  };

  const generationProps = genDefaults
    ? {
        kind: modelKind,
        settings: generation,
        defaults: genDefaults.defaults?.[modelKind] || {},
        contextSizes: genDefaults.context_sizes || [2048, 4096, 8192, 16384, 32768, 65536, 131072],
        model: currentModel,
        models,
        onChange: updateGeneration,
        onReset: resetGeneration,
        onSaveDefault: saveGenerationDefault,
        onSelectModel: setSelectedModel,
        onModelsChanged: refreshModels,
      }
    : null;

  return (
    <div className="app-layout">
      {showSplash && <SplashScreen onStart={dismissSplash} />}
      <Sidebar
        models={models}
        selectedModel={selectedModel}
        onModelChange={setSelectedModel}
        tools={tools}
        selectedTools={selectedTools}
        onToggleTool={toggleTool}
        mcpServers={mcpServers}
        onReconnectMCP={handleReconnectMCP}
        onOpenMarketplace={() => setShowMarketplace(true)}
        onRemoveMCP={handleRemoveMCP}
        onOpenMCPResources={(srv) => setResourcesServer(srv)}
        files={files}
        selectedFiles={selectedFiles}
        onToggleFile={toggleFile}
        onUpload={handleUpload}
        onDeleteFile={handleDeleteFile}
        onPreviewFile={handlePreviewFile}
        uploadProgress={uploadProgress}
        conversations={conversations}
        activeConversationId={activeConversationId}
        onSelectConversation={handleSelectConversation}
        onDeleteConversation={handleDeleteConversation}
        onNewChat={handleNewChat}
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        toolMode={toolMode}
        onToolModeChange={handleToolModeChange}
        lastToolSelection={lastToolSelection}
      />

      {showMarketplace && (
        <MCPMarketplacePanel onClose={() => setShowMarketplace(false)} onAdded={refreshToolsAndMcp} />
      )}
      {resourcesServer && (
        <MCPResourcesDialog
          server={resourcesServer}
          onClose={() => setResourcesServer(null)}
          onIngested={async () => setFiles(await fetchFiles())}
        />
      )}

      <div className="main-content">
        {/* Header */}
        <div className="header">
          <div className="header-left">
            <button className="sidebar-toggle" onClick={() => setSidebarOpen(!sidebarOpen)}>
              <Menu size={20} />
            </button>
            <span className="header-title">Conversation</span>
            {selectedModel && (
              <span className="header-model-badge" title={currentModel?.provider}>
                {selectedModel}
              </span>
            )}
            {currentModel && <ModelBadges model={currentModel} />}
            {compactionState?.covers > 0 && (
              <span className="header-compaction" title="Older turns were summarised to fit the context window">
                <Scissors size={12} /> {compactionState.covers} msgs compacted
              </span>
            )}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, color: isConnected ? "var(--success)" : "var(--error)", fontSize: 12 }}>
            {isConnected ? <Wifi size={14} /> : <WifiOff size={14} />}
            {isConnected ? "Connected" : "Disconnected"}
          </div>
        </div>

        {/* Chat Area */}
        <div className="chat-area" ref={chatAreaRef}>
          <div className="chat-container">
            {messages.length === 0 ? (
              <div className="empty-state">
                <div className="empty-state-icon">
                  <MessageCircle size={24} />
                </div>
                <div className="empty-state-title">Start a conversation</div>
                <div className="empty-state-hint">
                  Select a model and tools from the sidebar, then type your message below. Upload files for RAG-enhanced
                  responses, attach images for vision models, and tune context size and sampling from the settings
                  control next to the prompt box.
                </div>
              </div>
            ) : (
              <>
                {messages.map((msg, i) => (
                  <ChatMessage key={i} message={msg} onOpenCanvas={handleOpenCanvas} onRunCode={handleRunCode} />
                ))}
                {isTyping && <TypingIndicator />}
              </>
            )}
            <div ref={chatEndRef} />
          </div>
        </div>

        {/* Input */}
        <ChatInput
          value={inputValue}
          onChange={setInputValue}
          onSend={handleSend}
          onUpload={handleUpload}
          disabled={isStreaming || !isConnected}
          selectedFiles={selectedFiles}
          onRemoveFile={removeFile}
          uploadProgress={uploadProgress}
          conversationStats={conversationStats}
          lastResponseStats={lastResponseStats}
          llmStatus={llmStatus}
          isStreaming={isStreaming}
          notice={notice}
          images={pendingImages}
          onImagesChange={setPendingImages}
          visionCapable={visionCapable}
          generationProps={generationProps}
          contextInfo={contextInfo}
        />
      </div>

      {/* Right-side Canvas Panel */}
      {canvasData && (
        <>
          <div
            className="canvas-divider"
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize canvas panel"
            tabIndex={0}
            onMouseDown={handleDividerMouseDown}
            onKeyDown={handleDividerKeyDown}
          />
          <CanvasPanel
            image={canvasData.image}
            figureJson={canvasData.figureJson}
            content={canvasData.content}
            contentType={canvasData.contentType}
            chunks={canvasData.chunks}
            artifacts={canvasData.artifacts}
            filename={canvasData.filename}
            previewUrl={canvasData.previewUrl}
            sandboxSessionId={canvasData.sandboxSessionId}
            csvPreview={canvasData.csvPreview}
            lang={canvasData.lang}
            title={canvasData.title}
            onClose={() => setCanvasData(null)}
            onOpenCanvas={handleOpenCanvas}
            style={canvasWidth ? { width: canvasWidth } : undefined}
          />
        </>
      )}
    </div>
  );
}
