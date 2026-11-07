// Lightweight syntax highlighting: highlight.js core + the languages the
// chat is most likely to see. Unknown languages fall back to plain text.
import hljs from 'highlight.js/lib/core'
import python from 'highlight.js/lib/languages/python'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import yaml from 'highlight.js/lib/languages/yaml'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import java from 'highlight.js/lib/languages/java'
import c from 'highlight.js/lib/languages/c'
import cpp from 'highlight.js/lib/languages/cpp'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'
import sql from 'highlight.js/lib/languages/sql'
import markdown from 'highlight.js/lib/languages/markdown'
import dockerfile from 'highlight.js/lib/languages/dockerfile'
import r from 'highlight.js/lib/languages/r'
import diff from 'highlight.js/lib/languages/diff'
import plaintext from 'highlight.js/lib/languages/plaintext'

const LANGS = {
  python, py: python, javascript, js: javascript, jsx: javascript, typescript, ts: typescript, tsx: typescript,
  bash, sh: bash, shell: bash, zsh: bash, json, yaml, yml: yaml, xml, html: xml, svg: xml, css, java,
  c, cpp, 'c++': cpp, cxx: cpp, go, golang: go, rust, rs: rust, sql, markdown, md: markdown,
  dockerfile, r, diff, plaintext, text: plaintext, txt: plaintext,
}

let registered = false
function ensureRegistered() {
  if (registered) return
  Object.entries(LANGS).forEach(([name, def]) => {
    if (!hljs.getLanguage(name)) hljs.registerLanguage(name, def)
  })
  registered = true
}

// Returns HTML (already escaped) for ``code``. Large blocks are left
// unhighlighted to keep streaming cheap.
export function highlightCode(code, lang) {
  ensureRegistered()
  const key = (lang || '').toLowerCase().trim()
  if (!code || code.length > 60000) return null
  try {
    if (key && hljs.getLanguage(key)) {
      return hljs.highlight(code, { language: key, ignoreIllegals: true }).value
    }
    if (!key && code.length < 4000) {
      const auto = hljs.highlightAuto(code, ['python', 'javascript', 'json', 'bash', 'xml'])
      return auto.relevance > 5 ? auto.value : null
    }
  } catch (_e) {
    /* fall through */
  }
  return null
}

// Languages the sandbox can execute (mirrors backend SUPPORTED_LANGUAGES).
export const RUNNABLE_LANGS = {
  python: 'python', py: 'python', python3: 'python',
  javascript: 'javascript', js: 'javascript', node: 'javascript',
  typescript: 'typescript', ts: 'typescript',
  bash: 'bash', sh: 'bash', shell: 'bash', zsh: 'bash',
  java: 'java', c: 'c', cpp: 'cpp', 'c++': 'cpp', cxx: 'cpp',
  go: 'go', golang: 'go', rust: 'rust', rs: 'rust', r: 'r',
  html: 'html', htm: 'html',
}

export function runnableLanguage(lang) {
  return RUNNABLE_LANGS[(lang || '').toLowerCase().trim()] || null
}
