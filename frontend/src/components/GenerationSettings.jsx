import React, { useEffect, useMemo, useState } from 'react'
import { X, RotateCcw, Save, Download, Loader2, Cpu, Cloud, Layers } from 'lucide-react'
import { pullModel } from '../hooks/api'

const KEEP_ALIVE_OPTIONS = [
  { value: '0', label: 'Unload immediately' },
  { value: '5m', label: '5 minutes' },
  { value: '10m', label: '10 minutes' },
  { value: '30m', label: '30 minutes' },
  { value: '1h', label: '1 hour' },
  { value: '-1', label: 'Keep loaded' },
]

function Field({ label, hint, children }) {
  return (
    <label className="gen-field">
      <span className="gen-field-label">
        {label}
        {hint && <span className="gen-field-hint">{hint}</span>}
      </span>
      {children}
    </label>
  )
}

function NumberInput({ value, onChange, min, max, step = 1, placeholder }) {
  return (
    <input
      type="number"
      className="gen-input"
      value={value ?? ''}
      min={min}
      max={max}
      step={step}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
    />
  )
}

function Slider({ value, onChange, min, max, step, fallback }) {
  const v = value ?? fallback
  return (
    <div className="gen-slider">
      <input type="range" min={min} max={max} step={step} value={v} onChange={(e) => onChange(Number(e.target.value))} />
      <input
        type="number"
        className="gen-input gen-input-small"
        min={min}
        max={max}
        step={step}
        value={v}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
      />
    </div>
  )
}

export function formatTokens(n) {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1000) return `${Math.round(n / 1000)}k`
  return String(n)
}

// Prompt-box settings control: context size, sampling, thinking,
// quantization variant (Ollama), compaction.
export default function GenerationSettings({
  kind,
  settings,
  defaults,
  contextSizes,
  model,
  models,
  contextInfo,
  onChange,
  onReset,
  onSaveDefault,
  onSelectModel,
  onModelsChanged,
  onClose,
}) {
  const [pullTag, setPullTag] = useState('')
  const [pullState, setPullState] = useState(null) // {status, percent, error}

  useEffect(() => {
    const handleEsc = (e) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', handleEsc)
    return () => document.removeEventListener('keydown', handleEsc)
  }, [onClose])

  const comp = settings.compaction || {}
  const set = (key, value) => onChange({ [key]: value })
  const setComp = (key, value) => onChange({ compaction: { ...comp, [key]: value } })

  const isLocal = kind === 'local'
  const caps = model?.capabilities || {}
  const modelCtx = model?.context_length

  // Quantization variants: other tags of the same base model on the server.
  const variants = useMemo(() => {
    if (!model || !isLocal) return []
    const base = model.id.split(':')[0]
    return (models || []).filter(
      (m) => m.provider === model.provider && m.id.split(':')[0] === base && m.remote_available !== false,
    )
  }, [model, models, isLocal])

  const budget = contextInfo?.budget || (isLocal ? settings.num_ctx : settings.context_window) || modelCtx
  const used = contextInfo?.lastPromptTokens ?? contextInfo?.estimated_prompt_tokens
  const usedPct = budget && used ? Math.min(100, Math.round((used / budget) * 100)) : null

  const handlePull = async () => {
    const tag = pullTag.trim()
    if (!tag) return
    setPullState({ status: 'starting', percent: 0 })
    try {
      await pullModel(tag, (ev) => {
        if (ev.error) {
          setPullState({ status: 'error', error: ev.error })
          return
        }
        const pct = ev.total ? Math.round(((ev.completed || 0) / ev.total) * 100) : null
        setPullState({ status: ev.status || 'pulling', percent: pct })
      })
      setPullState((s) => (s?.status === 'error' ? s : { status: 'done', percent: 100 }))
      onModelsChanged && (await onModelsChanged())
      onSelectModel && onSelectModel(tag)
    } catch (e) {
      setPullState({ status: 'error', error: e.message })
    }
  }

  return (
    <div className="gen-popup" role="dialog" aria-label="Generation settings">
      <div className="gen-popup-header">
        <span className="gen-popup-title">
          {isLocal ? <Cpu size={14} /> : <Cloud size={14} />}
          {isLocal ? 'Local model settings' : 'Cloud model settings'}
          {model && <span className="gen-popup-model">{model.name || model.id}</span>}
        </span>
        <button className="gen-icon-btn" onClick={onClose} title="Close">
          <X size={14} />
        </button>
      </div>

      {/* Context meter */}
      <div className="gen-context-meter">
        <div className="gen-context-meter-row">
          <span>
            Context {used != null ? `${formatTokens(used)} used` : ''}
            {budget ? ` of ${formatTokens(budget)} tokens` : ''}
            {modelCtx && budget && budget > modelCtx ? ' · exceeds model max' : ''}
          </span>
          {usedPct != null && <span>{usedPct}%</span>}
        </div>
        <div className="gen-context-bar">
          <div
            className={`gen-context-fill ${usedPct > 85 ? 'hot' : ''}`}
            style={{ width: `${usedPct ?? 0}%` }}
          />
        </div>
        {modelCtx && <div className="gen-field-hint">Model reports a maximum context of {formatTokens(modelCtx)} tokens.</div>}
      </div>

      <div className="gen-popup-body">
        <div className="gen-section-label">
          <Layers size={11} /> Context & length
        </div>
        {isLocal ? (
          <Field label="Context size (num_ctx)" hint="tokens the model can see">
            <div className="gen-row">
              <select
                className="gen-input"
                value={contextSizes.includes(Number(settings.num_ctx)) ? String(settings.num_ctx) : 'custom'}
                onChange={(e) => e.target.value !== 'custom' && set('num_ctx', Number(e.target.value))}
              >
                {contextSizes.map((n) => (
                  <option key={n} value={n}>
                    {n.toLocaleString()} {modelCtx && n > modelCtx ? '(above model max)' : ''}
                  </option>
                ))}
                <option value="custom">Custom…</option>
              </select>
              <NumberInput value={settings.num_ctx} min={512} max={1048576} step={512} onChange={(v) => set('num_ctx', v)} />
            </div>
          </Field>
        ) : (
          <Field label="Context window" hint="used for compaction decisions">
            <NumberInput value={settings.context_window} min={4096} max={2000000} step={1024} onChange={(v) => set('context_window', v)} />
          </Field>
        )}
        <Field label="Max response tokens" hint={isLocal ? 'num_predict' : 'max_tokens'}>
          <NumberInput value={settings.max_tokens} min={64} max={128000} step={64} onChange={(v) => set('max_tokens', v)} />
        </Field>

        <div className="gen-section-label">Sampling</div>
        <Field label="Temperature">
          <Slider value={settings.temperature} fallback={defaults.temperature ?? 0.7} min={0} max={2} step={0.05} onChange={(v) => set('temperature', v)} />
        </Field>
        <Field label="Top-p">
          <Slider value={settings.top_p} fallback={defaults.top_p ?? 1} min={0} max={1} step={0.01} onChange={(v) => set('top_p', v)} />
        </Field>
        {isLocal && (
          <>
            <Field label="Top-k">
              <NumberInput value={settings.top_k} min={0} max={500} onChange={(v) => set('top_k', v)} />
            </Field>
            <Field label="Repeat penalty">
              <NumberInput value={settings.repeat_penalty} min={0.5} max={2} step={0.05} onChange={(v) => set('repeat_penalty', v)} />
            </Field>
            <Field label="Keep model loaded" hint="keep_alive">
              <select className="gen-input" value={String(settings.keep_alive ?? '10m')} onChange={(e) => set('keep_alive', e.target.value)}>
                {KEEP_ALIVE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>
          </>
        )}
        <Field label="Seed" hint="blank = random">
          <NumberInput value={settings.seed} min={0} onChange={(v) => set('seed', v)} placeholder="random" />
        </Field>

        <div className="gen-section-label">Thinking / reasoning</div>
        <Field label="Mode" hint={caps.thinking ? 'model supports thinking' : ''}>
          <div className="gen-row">
            <select
              className="gen-input"
              value={settings.thinking === true ? 'on' : settings.thinking === false ? 'off' : 'auto'}
              onChange={(e) => set('thinking', e.target.value === 'auto' ? null : e.target.value === 'on')}
            >
              <option value="auto">Auto (per model config)</option>
              <option value="on">On</option>
              <option value="off">Off</option>
            </select>
            <select className="gen-input" value={settings.thinking_level || ''} onChange={(e) => set('thinking_level', e.target.value || null)}>
              <option value="">Default effort</option>
              {!isLocal && <option value="minimal">Minimal</option>}
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
          </div>
        </Field>
        {!isLocal && model?.provider === 'anthropic' && (
          <Field label="Thinking budget" hint="tokens (Anthropic)">
            <NumberInput value={settings.thinking_budget_tokens} min={1024} max={100000} step={256} onChange={(v) => set('thinking_budget_tokens', v)} />
          </Field>
        )}

        {isLocal && (
          <>
            <div className="gen-section-label">Quantization / variant</div>
            <Field label="Installed variants" hint={model?.quantization ? `current: ${model.quantization}` : ''}>
              <select className="gen-input" value={model?.id || ''} onChange={(e) => onSelectModel && onSelectModel(e.target.value)}>
                {variants.length === 0 && model && <option value={model.id}>{model.id}</option>}
                {variants.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.id}
                    {v.quantization ? ` · ${v.quantization}` : ''}
                    {v.parameter_size ? ` · ${v.parameter_size}` : ''}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Pull another tag" hint="e.g. llama3.2:3b-instruct-q8_0">
              <div className="gen-row">
                <input
                  type="text"
                  className="gen-input"
                  placeholder={model ? `${model.id.split(':')[0]}:q8_0` : 'model:tag'}
                  value={pullTag}
                  onChange={(e) => setPullTag(e.target.value)}
                />
                <button className="gen-btn" onClick={handlePull} disabled={!pullTag.trim() || pullState?.status === 'pulling'} title="Pull from the Ollama library">
                  {pullState && pullState.status !== 'done' && pullState.status !== 'error' ? <Loader2 size={13} className="spin" /> : <Download size={13} />}
                  Pull
                </button>
              </div>
              {pullState && (
                <div className={`gen-field-hint ${pullState.status === 'error' ? 'error' : ''}`}>
                  {pullState.status === 'error'
                    ? pullState.error
                    : `${pullState.status}${pullState.percent != null ? ` · ${pullState.percent}%` : ''}`}
                </div>
              )}
            </Field>
          </>
        )}

        <div className="gen-section-label">Context compaction</div>
        <Field label="When the chat outgrows the context" hint="runs before each request">
          <select className="gen-input" value={comp.mode || 'off'} onChange={(e) => setComp('mode', e.target.value)}>
            <option value="off">Off — send everything (may fail or truncate silently)</option>
            <option value="truncate">Truncate — drop the oldest turns</option>
            <option value="summarize">Summarize — replace old turns with a summary</option>
          </select>
        </Field>
        {comp.mode && comp.mode !== 'off' && (
          <>
            <Field label="Trigger at" hint="% of context size">
              <Slider value={comp.threshold != null ? Math.round(comp.threshold * 100) : null} fallback={80} min={30} max={95} step={5} onChange={(v) => setComp('threshold', v / 100)} />
            </Field>
            <Field label="Always keep the last N messages">
              <NumberInput value={comp.keep_recent} min={2} max={40} onChange={(v) => setComp('keep_recent', v)} />
            </Field>
          </>
        )}
      </div>

      <div className="gen-popup-footer">
        <button className="gen-btn" onClick={onReset} title="Restore the saved defaults">
          <RotateCcw size={13} /> Reset
        </button>
        <button className="gen-btn primary" onClick={onSaveDefault} title={`Use these values as the default for all ${kind} models`}>
          <Save size={13} /> Save as {kind} default
        </button>
      </div>
    </div>
  )
}
