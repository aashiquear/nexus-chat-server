import React, { useMemo, useState } from 'react'
import { ArrowUpDown, ArrowUp, ArrowDown, ChevronLeft, ChevronRight, Search } from 'lucide-react'

// RFC-4180-ish CSV parser (quotes, escaped quotes, embedded newlines).
export function parseDelimited(text, delimiter) {
  const rows = []
  let row = []
  let field = ''
  let inQuotes = false
  const delim = delimiter || detectDelimiter(text)
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"'
          i++
        } else {
          inQuotes = false
        }
      } else {
        field += ch
      }
    } else if (ch === '"') {
      inQuotes = true
    } else if (ch === delim) {
      row.push(field)
      field = ''
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++
      row.push(field)
      field = ''
      if (row.length > 1 || row[0] !== '') rows.push(row)
      row = []
    } else {
      field += ch
    }
  }
  if (field !== '' || row.length) {
    row.push(field)
    rows.push(row)
  }
  return rows
}

export function detectDelimiter(text) {
  const sample = text.slice(0, 5000).split('\n').slice(0, 10)
  const candidates = [',', '\t', ';', '|']
  let best = ','
  let bestScore = -1
  for (const d of candidates) {
    const counts = sample.map((l) => l.split(d).length - 1)
    const nonZero = counts.filter((c) => c > 0)
    if (!nonZero.length) continue
    const consistent = nonZero.every((c) => c === nonZero[0])
    const score = nonZero[0] * (consistent ? 2 : 1)
    if (score > bestScore) {
      bestScore = score
      best = d
    }
  }
  return best
}

function isNumeric(v) {
  if (v === null || v === undefined || v === '') return false
  return typeof v === 'number' || (!isNaN(v) && !isNaN(parseFloat(v)))
}

// Interactive table: sortable columns, filter box, paging.
// ``columns``: [{name, kind?}] ; ``rows``: array of arrays.
export default function CsvTable({ columns, rows, pageSize = 50, compact = false, footer }) {
  const [sort, setSort] = useState(null) // {col, dir}
  const [filter, setFilter] = useState('')
  const [page, setPage] = useState(0)

  const filtered = useMemo(() => {
    if (!filter) return rows
    const q = filter.toLowerCase()
    return rows.filter((r) => r.some((v) => v !== null && v !== undefined && String(v).toLowerCase().includes(q)))
  }, [rows, filter])

  const sorted = useMemo(() => {
    if (!sort) return filtered
    const idx = sort.col
    const numeric = filtered.slice(0, 50).every((r) => r[idx] === null || r[idx] === '' || isNumeric(r[idx]))
    const copy = [...filtered]
    copy.sort((a, b) => {
      const x = a[idx]
      const y = b[idx]
      if (x === null || x === undefined || x === '') return 1
      if (y === null || y === undefined || y === '') return -1
      const cmp = numeric ? parseFloat(x) - parseFloat(y) : String(x).localeCompare(String(y))
      return sort.dir === 'asc' ? cmp : -cmp
    })
    return copy
  }, [filtered, sort])

  const pages = Math.max(1, Math.ceil(sorted.length / pageSize))
  const current = Math.min(page, pages - 1)
  const slice = sorted.slice(current * pageSize, (current + 1) * pageSize)

  const toggleSort = (col) => {
    setSort((prev) => {
      if (!prev || prev.col !== col) return { col, dir: 'asc' }
      if (prev.dir === 'asc') return { col, dir: 'desc' }
      return null
    })
  }

  return (
    <div className={`csv-table ${compact ? 'compact' : ''}`}>
      <div className="csv-table-toolbar">
        <div className="csv-table-search">
          <Search size={12} />
          <input
            type="text"
            value={filter}
            placeholder="Filter rows…"
            onChange={(e) => {
              setFilter(e.target.value)
              setPage(0)
            }}
          />
        </div>
        <span className="csv-table-meta">
          {sorted.length.toLocaleString()} rows × {columns.length} cols
          {footer ? ` · ${footer}` : ''}
        </span>
        {pages > 1 && (
          <div className="csv-table-pager">
            <button onClick={() => setPage(Math.max(0, current - 1))} disabled={current === 0} title="Previous page">
              <ChevronLeft size={13} />
            </button>
            <span>
              {current + 1} / {pages}
            </span>
            <button onClick={() => setPage(Math.min(pages - 1, current + 1))} disabled={current >= pages - 1} title="Next page">
              <ChevronRight size={13} />
            </button>
          </div>
        )}
      </div>
      <div className="csv-table-scroll">
        <table>
          <thead>
            <tr>
              <th className="csv-row-index">#</th>
              {columns.map((c, i) => {
                const active = sort && sort.col === i
                const Icon = !active ? ArrowUpDown : sort.dir === 'asc' ? ArrowUp : ArrowDown
                return (
                  <th key={i} onClick={() => toggleSort(i)} title={c.dtype ? `${c.name} (${c.dtype})` : c.name}>
                    <span>{c.name}</span>
                    <Icon size={11} className={`csv-sort-icon ${active ? 'active' : ''}`} />
                    {c.kind && <span className="csv-col-kind">{c.kind}</span>}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {slice.map((r, ri) => (
              <tr key={ri}>
                <td className="csv-row-index">{current * pageSize + ri + 1}</td>
                {columns.map((_c, ci) => {
                  const v = r[ci]
                  const empty = v === null || v === undefined || v === ''
                  return (
                    <td key={ci} className={`${isNumeric(v) ? 'num' : ''} ${empty ? 'empty' : ''}`}>
                      {empty ? '∅' : String(v)}
                    </td>
                  )
                })}
              </tr>
            ))}
            {slice.length === 0 && (
              <tr>
                <td colSpan={columns.length + 1} className="csv-empty">
                  No rows match the filter.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// Build {columns, rows} from raw CSV text.
export function tableFromText(text, delimiter) {
  const parsed = parseDelimited(text, delimiter)
  if (!parsed.length) return { columns: [], rows: [] }
  const header = parsed[0]
  const width = Math.max(...parsed.map((r) => r.length))
  const columns = Array.from({ length: width }, (_, i) => ({ name: header[i] ?? `col${i + 1}` }))
  const rows = parsed.slice(1).map((r) => Array.from({ length: width }, (_, i) => r[i] ?? ''))
  return { columns, rows }
}
