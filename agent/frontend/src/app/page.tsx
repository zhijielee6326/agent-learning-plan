'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { Message, Product, TocItem, ChatSession } from '@/lib/types'
import { API_BASE, DEFAULT_EDITOR_CONTENT, STORAGE_KEY, QUICK_QUERIES } from '@/lib/constants'
import { parseToc, markdownToHTML } from '@/lib/markdown'

// ========== 主组件 ==========
export default function Home() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [editorContent, setEditorContent] = useState<string>(DEFAULT_EDITOR_CONTENT)
  const [sources, setSources] = useState<string[]>([])
  const [products, setProducts] = useState<Product[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [clauseProgress, setClauseProgress] = useState<string>('')
  // 打字机效果：缓冲区 vs 显示区
  const streamBufferRef = useRef<string>('')
  const displayedLenRef = useRef<number>(0)
  const typingRafRef = useRef<number>(0)
  const typingMsgIdRef = useRef<string>('')

  // 会话管理
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [activeSessionId, setActiveSessionId] = useState('')
  const [showSessionList, setShowSessionList] = useState(false)

  // 编辑器面板是否可见（默认隐藏，条款生成完后自动打开）
  const [showEditor, setShowEditor] = useState(false)
  const [tocItems, setTocItems] = useState<TocItem[]>([])
  const [showToc, setShowToc] = useState(true)
  const [isEditing, setIsEditing] = useState(false)
  const [isEditingAI, setIsEditingAI] = useState(false)

  // AI 弹窗
  const [selectedText, setSelectedText] = useState('')
  const [selectionPosition, setSelectionPosition] = useState<{ x: number; y: number } | null>(null)
  const [aiPopupMode, setAiPopupMode] = useState<'quick' | 'custom' | 'result'>('quick')
  const [customInstruction, setCustomInstruction] = useState('')
  const [aiResult, setAiResult] = useState('')

  // 导出加载状态
  const [isExporting, setIsExporting] = useState(false)

  // 释义管理面板
  const [showDefPanel, setShowDefPanel] = useState(false)
  const [editingDefIdx, setEditingDefIdx] = useState<number | null>(null)
  const [editingDefText, setEditingDefText] = useState('')

  // Refs
  const chatEndRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const editorRef = useRef<HTMLDivElement>(null)
  const lastRenderedRef = useRef('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // 撤销历史栈（用 state 驱动按钮 disabled 状态）
  const [undoCount, setUndoCount] = useState(0)
  const undoStackRef = useRef<string[]>([])
  const MAX_UNDO = 50

  const pushUndo = useCallback((content: string) => {
    const stack = undoStackRef.current
    if (stack.length > 0 && stack[stack.length - 1] === content) return
    stack.push(content)
    if (stack.length > MAX_UNDO) stack.shift()
    setUndoCount(stack.length)
  }, [])

  const handleUndo = useCallback(() => {
    const stack = undoStackRef.current
    if (stack.length === 0) return
    const prev = stack.pop()!
    setUndoCount(stack.length)
    lastRenderedRef.current = ''
    setEditorContent(prev)
  }, [])

  // ========== 释义管理 ==========
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  type DefItem = { term: string; content: string; source: string; fullLine: string }

  const parseDefinitions = useCallback((md: string): DefItem[] => {
    const defs: DefItem[] = []
    const lines = md.split('\n')
    let inDefSection = false
    for (const line of lines) {
      if (/^##\s+释义/.test(line)) { inDefSection = true; continue }
      if (inDefSection && /^##\s/.test(line)) break
      if (!inDefSection) continue
      const match = line.match(/^第[一二三四五六七八九十百零\d]+条\s+(.+?)[：:]+([\s\S]+)$/)
      if (match) {
        const term = match[1].trim()
        const rest = match[2].trim()
        const srcMatch = rest.match(/\[([^\]]+?)\]\s*$/)
        const source = srcMatch ? srcMatch[1] : ''
        const content = srcMatch ? rest.slice(0, srcMatch.index).trim() : rest
        defs.push({ term, content, source, fullLine: line })
      }
    }
    return defs
  }, [])

  const getDefItems = useCallback((): DefItem[] => {
    return parseDefinitions(editorContent)
  }, [editorContent, parseDefinitions])

  const deleteDefItem = useCallback((idx: number) => {
    pushUndo(editorContent)
    const lines = editorContent.split('\n')
    const newLines: string[] = []
    let inDefSection = false
    let defCount = 0
    for (const line of lines) {
      if (/^##\s+释义/.test(line)) { inDefSection = true; newLines.push(line); continue }
      if (inDefSection && /^##\s/.test(line)) { inDefSection = false; newLines.push(line); continue }
      if (inDefSection && /^第[一二三四五六七八九十百零\d]+条/.test(line)) {
        if (defCount === idx) { defCount++; continue }  // skip deleted item
        defCount++
      }
      newLines.push(line)
    }
    setEditorContent(newLines.join('\n'))
    lastRenderedRef.current = ''
  }, [editorContent, pushUndo])

  const saveDefEdit = useCallback((idx: number, newText: string) => {
    pushUndo(editorContent)
    const lines = editorContent.split('\n')
    const newLines: string[] = []
    let inDefSection = false
    let defCount = 0
    for (const line of lines) {
      if (/^##\s+释义/.test(line)) { inDefSection = true; newLines.push(line); continue }
      if (inDefSection && /^##\s/.test(line)) { inDefSection = false; newLines.push(line); continue }
      if (inDefSection && /^第[一二三四五六七八九十百零\d]+条/.test(line)) {
        if (defCount === idx) { newLines.push(newText); defCount++; continue }
        defCount++
      }
      newLines.push(line)
    }
    setEditorContent(newLines.join('\n'))
    lastRenderedRef.current = ''
    setEditingDefIdx(null)
  }, [editorContent, pushUndo])

  // ========== 会话持久化 ==========
  const saveToStorage = useCallback((s: ChatSession[]) => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(s)) } catch {}
  }, [])

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved) {
        const parsed = JSON.parse(saved) as ChatSession[]
        if (parsed.length > 0) {
          setSessions(parsed)
          setActiveSessionId(parsed[0].id)
          setMessages(parsed[0].messages)
          setEditorContent(parsed[0].editorContent)
          setSources(parsed[0].sources)
          return
        }
      }
    } catch {}
    createNewSession()
  }, [])

  // 自动保存
  useEffect(() => {
    if (!activeSessionId) return
    setSessions(prev => {
      const updated = prev.map(s => {
        if (s.id !== activeSessionId) return s
        const title = s.messages.length === 0 && messages.length > 0 && messages[0].role === 'user'
          ? messages[0].content.slice(0, 20) + (messages[0].content.length > 20 ? '...' : '')
          : s.title
        return { ...s, messages, editorContent, sources, title, updatedAt: Date.now() }
      })
      saveToStorage(updated)
      return updated
    })
  }, [messages, editorContent, sources, activeSessionId, saveToStorage])

  function createNewSession() {
    const id = 'session_' + Date.now()
    const ns: ChatSession = { id, title: '新对话', messages: [], editorContent: DEFAULT_EDITOR_CONTENT, sources: [], createdAt: Date.now(), updatedAt: Date.now() }
    setSessions(prev => {
      const updated = [ns, ...prev]
      saveToStorage(updated)
      return updated
    })
    setActiveSessionId(id)
    setMessages([])
    setInput('')
    setEditorContent(DEFAULT_EDITOR_CONTENT)
    setSources([])
    lastRenderedRef.current = ''
    setShowEditor(false)
  }

  function switchSession(id: string) {
    const session = sessions.find(s => s.id === id)
    if (!session) return
    setActiveSessionId(id)
    setMessages(session.messages)
    setEditorContent(session.editorContent)
    setSources(session.sources)
    lastRenderedRef.current = ''
    setShowSessionList(false)
  }

  function deleteSession(id: string, e: React.MouseEvent) {
    e.stopPropagation()
    const remaining = sessions.filter(s => s.id !== id)
    if (remaining.length === 0) {
      createNewSession()
    } else if (id === activeSessionId) {
      switchSession(remaining[0].id)
    }
    setSessions(remaining)
    saveToStorage(remaining)
  }

  // ========== 目录 & 编辑器 ==========
  useEffect(() => { setTocItems(parseToc(editorContent)) }, [editorContent])

  // 初始渲染
  useEffect(() => {
    if (editorRef.current && !lastRenderedRef.current) {
      editorRef.current.innerHTML = markdownToHTML(editorContent)
      lastRenderedRef.current = editorContent
    }
  }, [])

  // 内容更新时渲染（非编辑态 或 流式输出时）
  useEffect(() => {
    if (editorRef.current && editorContent !== lastRenderedRef.current) {
      if (isStreaming || !isEditing) {
        editorRef.current.innerHTML = markdownToHTML(editorContent)
        lastRenderedRef.current = editorContent
        if (isStreaming) { const p = editorRef.current.parentElement; if (p) p.scrollTop = p.scrollHeight }
      }
    }
  }, [editorContent, isStreaming, isEditing, showEditor])

  // 编辑器退出编辑态时，从 textarea 读取 Markdown 原文
  const exitEditMode = useCallback(() => {
    setIsEditing(false)
    if (textareaRef.current) {
      const md = textareaRef.current.value
      pushUndo(editorContent)
      lastRenderedRef.current = ''
      if (md !== editorContent) setEditorContent(md)
    }
  }, [editorContent, pushUndo])

  // Ctrl+Z 全局撤销快捷键
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'z') {
        if (isEditing) return
        e.preventDefault()
        handleUndo()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [isEditing, handleUndo])

  useEffect(() => { fetch(`${API_BASE}/products`).then(r => r.json()).then(d => setProducts(d.products || [])).catch(() => {}) }, [])
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  // 目录章节跳转
  const scrollToHeading = useCallback((id: string) => {
    if (editorRef.current) {
      const el = editorRef.current.querySelector(`#${id}`)
      if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'start' }); return }
    }
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [])

  // ========== 聊天 ==========
  const sendMessageStream = useCallback(async (query: string) => {
    pushUndo(editorContent)
    setIsLoading(true)
    setIsStreaming(true)
    let fullContent = ''
    let collectedSources: string[] = []
    const aid = Date.now().toString() + '-a'
    setMessages(prev => [...prev, { id: aid, role: 'assistant', content: '' }])
    const ac = new AbortController()
    abortRef.current = ac

    // 打字机效果
    streamBufferRef.current = ''
    displayedLenRef.current = 0
    typingMsgIdRef.current = aid
    const CHARS_PER_FRAME = 3

    if (typingRafRef.current) cancelAnimationFrame(typingRafRef.current)
    const typeFrame = () => {
      const buffer = streamBufferRef.current
      if (displayedLenRef.current < buffer.length) {
        displayedLenRef.current = Math.min(displayedLenRef.current + CHARS_PER_FRAME, buffer.length)
        const displayContent = buffer.slice(0, displayedLenRef.current)
        setMessages(prev => prev.map(m => m.id === typingMsgIdRef.current ? { ...m, content: displayContent } : m))
      }
      typingRafRef.current = requestAnimationFrame(typeFrame)
    }
    typingRafRef.current = requestAnimationFrame(typeFrame)

    let hasClauseContent = false

    try {
      const res = await fetch(`${API_BASE}/chat/stream`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, session_id: activeSessionId }),
        signal: ac.signal
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const reader = res.body?.getReader()
      if (!reader) throw new Error('No reader')
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() || ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const ev = JSON.parse(line.slice(6).trim())
            if (ev.type === 'content') {
              fullContent += ev.content
              streamBufferRef.current = fullContent
              // 条款生成场景：实时同步显示区，打字机直接追赶到最新位置
              if (hasClauseContent) {
                displayedLenRef.current = fullContent.length
              }
            }
            else if (ev.type === 'sources') collectedSources = Array.from(new Set([...collectedSources, ...(ev.sources || [])]))
            else if (ev.type === 'chapter_start') { setClauseProgress(`正在生成：${ev.chapter} (${ev.index + 1}/${ev.total})`); hasClauseContent = true }
            else if (ev.type === 'progress') { setClauseProgress(ev.message || ''); if (ev.message?.includes('章节') || ev.message?.includes('检索') || ev.message?.includes('险种')) hasClauseContent = true }
            else if (ev.type === 'error') { fullContent += (fullContent ? '\n\n' : '') + `> ⚠️ ${ev.message || ev.content || '服务调用失败'}`; streamBufferRef.current = fullContent; setClauseProgress('') }
            else if (ev.type === 'done') { setClauseProgress(''); if (ev.source_products) collectedSources = Array.from(new Set([...collectedSources, ...ev.source_products])) }
            if (collectedSources.length > 0) setSources(collectedSources)
          } catch {}
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') fullContent = fullContent || '（对话已取消）'
      else fullContent = '连接后端服务失败，请确认后端服务已启动（python main.py）'
      streamBufferRef.current = fullContent
    } finally {
      if (typingRafRef.current) {
        cancelAnimationFrame(typingRafRef.current)
        typingRafRef.current = 0
      }
      const finalContent = streamBufferRef.current
      displayedLenRef.current = finalContent.length
      setMessages(prev => prev.map(m => m.id === aid ? { ...m, content: finalContent } : m))
      setEditorContent(finalContent)
      setIsLoading(false)
      setIsStreaming(false)
      abortRef.current = null
      // 条款生成完成后自动打开编辑器
      if (hasClauseContent && finalContent.length > 100) {
        setShowEditor(true)
      }
    }
  }, [activeSessionId, editorContent, pushUndo])

  const handleSend = () => {
    const q = input.trim()
    if (!q || isLoading) return
    setMessages(prev => [...prev, { id: Date.now().toString(), role: 'user', content: q }])
    setInput('')
    sendMessageStream(q)
  }

  // ========== AI 编辑弹窗 ==========
  const handleMouseUp = useCallback(() => {
    setTimeout(() => {
      const sel = window.getSelection()
      if (!sel || sel.isCollapsed || !editorRef.current) {
        if (!isEditingAI) { setSelectionPosition(null); setSelectedText(''); setAiResult(''); setAiPopupMode('quick') }
        return
      }
      if (!editorRef.current.contains(sel.anchorNode)) return
      const text = sel.toString().trim()
      if (!text || text.length < 2) { setSelectionPosition(null); setSelectedText(''); return }
      const rect = sel.getRangeAt(0).getBoundingClientRect()
      setSelectionPosition({ x: rect.left + rect.width / 2, y: rect.top - 10 })
      setSelectedText(text)
      setAiResult('')
      setAiPopupMode('quick')
    }, 10)
  }, [isEditingAI])

  const handleAIEdit = useCallback(async (instruction: string) => {
    if (!selectedText) return
    setIsEditingAI(true)
    setAiPopupMode('result')
    let result = ''
    try {
      const res = await fetch(`${API_BASE}/edit/stream`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selected_text: selectedText, instruction, context: '' })
      })
      if (!res.ok) throw new Error()
      const reader = res.body?.getReader()
      if (!reader) throw new Error()
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        for (const line of buf.split('\n')) {
          if (!line.startsWith('data: ')) continue
          try { const ev = JSON.parse(line.slice(6).trim()); if (ev.type === 'content') { result += ev.content; setAiResult(result) } } catch {}
        }
        buf = ''
      }
    } catch { setAiResult('编辑失败，请重试') }
    finally { setIsEditingAI(false) }
  }, [selectedText])

  const handleAccept = useCallback(() => {
    if (!selectedText || !aiResult) return
    setEditorContent(prev => {
      if (prev.includes(selectedText)) {
        pushUndo(prev)
        return prev.replace(selectedText, aiResult)
      }
      const plainLines = prev.split('\n')
      for (let i = 0; i < plainLines.length; i++) {
        const plainLine = plainLines[i].replace(/\*\*/g, '').replace(/\*/g, '').replace(/`/g, '').replace(/^#+\s*/, '').replace(/^[-•]\s*/, '').replace(/^\d+\.\s*/, '').trim()
        if (plainLine.includes(selectedText) || selectedText.includes(plainLine)) {
          pushUndo(prev)
          const line = plainLines[i]
          const prefix = line.match(/^(\s*(?:#{1,4}\s*|[-•]\s*|\d+\.\s*)?)/)?.[1] || ''
          plainLines[i] = prefix + line.slice(prefix.length).replace(selectedText, aiResult)
          return plainLines.join('\n')
        }
      }
      const idx = prev.indexOf(selectedText.charAt(0))
      if (idx !== -1) {
        pushUndo(prev)
        const nearby = prev.slice(Math.max(0, idx - 50), idx + selectedText.length + 50)
        if (nearby.includes(selectedText)) {
          return prev.replace(selectedText, aiResult)
        }
      }
      pushUndo(prev)
      return prev + '\n' + aiResult
    })
    lastRenderedRef.current = ''
    setSelectedText(''); setAiResult(''); setSelectionPosition(null); setAiPopupMode('quick')
    window.getSelection()?.removeAllRanges()
  }, [selectedText, aiResult, pushUndo])

  const closePopup = useCallback(() => {
    setSelectionPosition(null); setSelectedText(''); setAiResult(''); setAiPopupMode('quick')
    window.getSelection()?.removeAllRanges()
  }, [])

  // ========== 导出 ==========
  const getExportTitle = useCallback(() => {
    const lines = editorContent.split('\n')
    for (const line of lines) {
      const trimmed = line.trim()
      if (trimmed.startsWith('# ')) return trimmed.slice(2).replace(/\*\*/g, '').trim()
      if (trimmed.startsWith('## ')) return trimmed.slice(3).replace(/\*\*/g, '').trim()
    }
    const firstUserMsg = messages.find(m => m.role === 'user')
    if (firstUserMsg) return firstUserMsg.content.slice(0, 30).replace(/[\\/:*?"<>|]/g, '')
    return '保险条款分析'
  }, [editorContent, messages])

  const exportMD = () => {
    const title = getExportTitle()
    const timestamp = new Date().toLocaleDateString('zh-CN').replace(/\//g, '-')
    const filename = `${title}_${timestamp}.md`
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([editorContent], { type: 'text/markdown;charset=utf-8' }))
    a.download = filename; a.click()
  }

  const exportWord = async () => {
    if (isExporting) return
    setIsExporting(true)
    try {
      const title = getExportTitle()
      const res = await fetch(`${API_BASE}/export/word`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: editorContent, title }) })
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}))
        throw new Error(errData.detail || '导出失败')
      }
      const contentDisposition = res.headers.get('content-disposition')
      let filename = `${title}.docx`
      if (contentDisposition) {
        const match = contentDisposition.match(/filename\*?=UTF-8''(.+?)(?:;|$)/)
        if (match) filename = decodeURIComponent(match[1])
      }
      const a = document.createElement('a')
      a.href = URL.createObjectURL(await res.blob())
      a.download = filename; a.click()
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Word导出失败，请确认后端服务已安装 python-docx')
    } finally {
      setIsExporting(false)
    }
  }

  const wordCount = editorContent.length

  // 打开编辑器
  const openEditor = useCallback((content?: string) => {
    const md = content || editorContent
    setEditorContent(md)
    lastRenderedRef.current = ''
    setShowEditor(true)
  }, [editorContent])

  const closeEditor = useCallback(() => {
    setShowEditor(false)
    setIsEditing(false)
  }, [])

  return (
    <div className="flex h-screen bg-white">
      {/* ========== 左侧：对话面板 ========== */}
      <div className={`flex flex-col bg-gradient-to-b from-slate-50 to-white shrink-0 shadow-sm transition-all duration-300 ${
        showEditor ? 'w-[340px] lg:w-[400px] border-r border-slate-200' : 'w-full'
      }`}>
        {/* 头部 */}
        <div className="px-4 py-3 border-b border-slate-200 bg-white/80 backdrop-blur-sm">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white text-sm font-bold shadow-md">AI</div>
            <div className="flex-1 min-w-0">
              <h1 className="text-[15px] font-bold text-slate-800 tracking-tight">保险产品精算智能体</h1>
              <p className="text-[10px] text-slate-400 mt-0.5">GLM-5.1 · RAG检索 · 条款溯源</p>
            </div>
            <div className="flex gap-1">
              <button onClick={() => setShowSessionList(!showSessionList)}
                className="px-2 py-1 text-[10px] rounded-md bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 transition-all">
                历史({sessions.length})
              </button>
              <button onClick={createNewSession}
                className="px-2 py-1 text-[10px] rounded-md bg-blue-50 text-blue-600 hover:bg-blue-100 border border-blue-200 transition-all">
                +新建
              </button>
            </div>
          </div>
        </div>

        {/* 会话历史列表 */}
        {showSessionList && (
          <div className="border-b border-slate-200 bg-white max-h-48 overflow-y-auto">
            {sessions.map(s => (
              <div key={s.id}
                onClick={() => switchSession(s.id)}
                className={`flex items-center gap-2 px-4 py-2 cursor-pointer border-b border-slate-50 hover:bg-blue-50 transition-all ${s.id === activeSessionId ? 'bg-blue-50 border-l-2 border-l-blue-500' : ''}`}>
                <span className="flex-1 text-[11px] text-slate-700 truncate">{s.title}</span>
                <span className="text-[9px] text-slate-300">{new Date(s.updatedAt).toLocaleDateString()}</span>
                <button onClick={(e) => deleteSession(s.id, e)}
                  className="text-[10px] text-slate-300 hover:text-red-400 ml-1">x</button>
              </div>
            ))}
          </div>
        )}

        {/* 消息列表 - 编辑器隐藏时居中显示 */}
        <div className={`flex-1 overflow-y-auto px-3 py-4 space-y-4 transition-all ${!showEditor ? 'flex flex-col items-center' : ''}`}>
          <div className={`w-full ${!showEditor ? 'max-w-3xl' : ''}`}>
          {messages.length === 0 && (
            <div className="space-y-2.5 pt-2">
              <div className="text-center py-4">
                <div className="w-12 h-12 mx-auto mb-3 rounded-2xl bg-gradient-to-br from-blue-50 to-purple-50 flex items-center justify-center text-2xl">AI</div>
                <p className="text-slate-500 text-[13px] font-medium">选择问题开始对话</p>
                <p className="text-slate-300 text-[11px] mt-1">或直接输入您的保险条款问题</p>
              </div>
              {QUICK_QUERIES.map((q, i) => (
                <button key={i} onClick={() => setInput(q.text)}
                  className="w-full text-left px-3.5 py-3 rounded-xl bg-white border border-slate-200/80 text-[13px] text-slate-600 hover:border-blue-300 hover:bg-blue-50/50 hover:shadow-sm transition-all group">
                  <span className="mr-2 group-hover:scale-110 inline-block transition-transform">{q.icon}</span>{q.text}
                </button>
              ))}
            </div>
          )}
          {messages.map(msg => (
            <div key={msg.id} className={`flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
              <div className={`max-w-[88%] px-3.5 py-2.5 text-[13px] leading-relaxed ${msg.role === 'user' ? 'msg-user' : 'msg-assistant'}`}>
                {msg.role === 'assistant' ? (
                  <div className="markdown-content">
                    <div dangerouslySetInnerHTML={{ __html: markdownToHTML(msg.content) || '' }} />
                    {isStreaming && msg.id === messages[messages.length - 1]?.id && <span className="cursor-blink" />}
                  </div>
                ) : msg.content}
              </div>
              {/* 操作按钮栏：生成完成后在消息下方显示 */}
              {msg.role === 'assistant' && msg.content && !isStreaming && !isLoading && (
                <div className="flex items-center gap-1 mt-2 ml-1">
                  <button
                    onClick={() => openEditor(msg.content)}
                    className="flex items-center gap-1 px-3 py-1.5 text-[11px] rounded-lg bg-blue-500 text-white hover:bg-blue-600 transition-all shadow-sm font-medium"
                  >
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
                    编辑文档
                  </button>
                  <button
                    onClick={() => { navigator.clipboard.writeText(msg.content); }}
                    className="flex items-center gap-1 px-2.5 py-1.5 text-[11px] rounded-lg bg-white text-slate-500 hover:bg-slate-50 hover:text-slate-700 border border-slate-200 transition-all"
                  >
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                    复制
                  </button>
                  <button
                    onClick={() => { const q = messages.filter(m => m.role === 'user').pop()?.content; if (q) sendMessageStream(q); }}
                    className="flex items-center gap-1 px-2.5 py-1.5 text-[11px] rounded-lg bg-white text-slate-500 hover:bg-slate-50 hover:text-slate-700 border border-slate-200 transition-all"
                  >
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
                    重新生成
                  </button>
                </div>
              )}
            </div>
          ))}
          {isLoading && !isStreaming && (
            <div className="flex justify-start">
              <div className="msg-assistant px-3.5 py-2.5 text-[13px] text-slate-400 flex items-center gap-2">
                <div className="animate-spin h-3.5 w-3.5 border-2 border-blue-500 border-t-transparent rounded-full" />检索中...
              </div>
            </div>
          )}
          <div ref={chatEndRef} />
          </div>
        </div>

        {/* 进度 */}
        {clauseProgress && (
          <div className="px-3 py-1.5 bg-blue-50 border-t border-blue-100">
            <div className="flex items-center gap-1.5 text-[10px] text-blue-600">
              <div className="animate-spin h-3 w-3 border-2 border-blue-500 border-t-transparent rounded-full" />{clauseProgress}
            </div>
          </div>
        )}

        {/* 输入区 */}
        <div className="px-3 py-3 border-t border-slate-200 bg-white/80 backdrop-blur-sm">
          <div className="flex gap-2 items-end">
            <textarea value={input} onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() } }}
              placeholder="输入您的保险条款问题..." rows={2}
              className="flex-1 resize-none rounded-xl border border-slate-200 px-3.5 py-2.5 text-[13px] leading-relaxed focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 transition-all placeholder:text-slate-300" />
            {isStreaming ? (
              <button onClick={() => abortRef.current?.abort()} className="px-4 py-2.5 rounded-xl text-[13px] font-medium bg-red-500 text-white hover:bg-red-600 shadow-sm transition-all">停止</button>
            ) : (
              <button onClick={handleSend} disabled={isLoading || !input.trim()}
                className="btn-primary px-4 py-2.5 rounded-xl text-[13px] font-medium disabled:opacity-40 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none transition-all">发送</button>
            )}
          </div>
          <p className="text-[10px] text-slate-300 mt-1.5 text-center">按 Enter 发送 · Shift+Enter 换行</p>
        </div>
      </div>

      {/* ========== 右侧：编辑区（仅条款生成后显示） ========== */}
      {showEditor && (
      <div className="flex-1 flex flex-col border-l border-slate-200">
        {/* 顶部工具栏 */}
        <div className="h-12 px-4 border-b border-slate-200 bg-white/80 backdrop-blur-sm flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2">
            <button onClick={closeEditor}
              className="px-2 py-1 text-[11px] rounded-md bg-slate-50 text-slate-500 hover:bg-red-50 hover:text-red-500 border border-slate-200 transition-all"
              title="关闭编辑器">
              关闭
            </button>
            <button onClick={() => setShowToc(!showToc)}
              className="px-2 py-1 text-[11px] rounded-md bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 transition-all">
              {showToc ? '收起目录' : '展开目录'}
            </button>
            <span className="text-[10px] text-slate-300">{wordCount} 字</span>
            {sources.length > 0 && <span className="text-[10px] text-blue-400">引用 {sources.length} 条</span>}
            <span className="text-[10px] text-slate-300">产品库 {products.length} 款</span>
          </div>
          <div className="flex gap-1.5">
            <button onClick={handleUndo} disabled={undoCount === 0}
              className="px-2 py-1 text-[11px] rounded-md bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
              title="撤销 (Ctrl+Z)">
              撤销
            </button>
            {isEditing ? (
              <button onClick={exitEditMode}
                className="px-2 py-1 text-[11px] rounded-md bg-green-50 text-green-600 hover:bg-green-100 border border-green-200 transition-all font-medium">
                完成编辑
              </button>
            ) : (
              <button onClick={() => { setIsEditing(true); lastRenderedRef.current = '' }}
                className="px-2 py-1 text-[11px] rounded-md bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 transition-all">
                编辑
              </button>
            )}
            <button onClick={exportMD} className="px-2 py-1 text-[11px] rounded-md bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 transition-all">导出 Markdown</button>
            <button onClick={exportWord} disabled={isExporting}
              className="px-2 py-1 text-[11px] rounded-md bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 transition-all disabled:opacity-30 disabled:cursor-not-allowed">
              {isExporting ? '导出中...' : '导出 Word'}
            </button>
            {getDefItems().length > 0 && (
              <button onClick={() => setShowDefPanel(!showDefPanel)}
                className={`px-2 py-1 text-[11px] rounded-md border transition-all ${showDefPanel ? 'bg-blue-50 text-blue-600 border-blue-200' : 'bg-slate-50 text-slate-500 hover:bg-slate-100 border-slate-200'}`}>
                释义管理 ({getDefItems().length})
              </button>
            )}
          </div>
        </div>

        {/* 编辑区主体 */}
        <div className="flex-1 flex overflow-hidden">
          {/* 目录大纲 */}
          {showToc && tocItems.length > 0 && (
            <div className="w-48 border-r border-slate-100 bg-slate-50/50 overflow-y-auto py-3 px-2 shrink-0">
              <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2 px-2">目录大纲</div>
              {tocItems.map((item, i) => (
                <button key={i} onClick={() => scrollToHeading(item.id)}
                  className={`w-full text-left px-2 py-1 rounded-md text-[11px] transition-all hover:bg-blue-50 hover:text-blue-600 mb-0.5 ${
                    item.level === 2 ? 'font-medium text-slate-700' : item.level === 3 ? 'pl-5 text-slate-500' : 'pl-8 text-slate-400'
                  }`}>
                  {item.text}
                </button>
              ))}
            </div>
          )}

          {/* 文档编辑区 + 释义面板 */}
            <div className="flex-1 overflow-y-auto relative">
              <div className="max-w-3xl mx-auto px-10 py-8">
                {isEditing ? (
                  <textarea ref={textareaRef}
                    defaultValue={editorContent}
                    className="w-full min-h-[600px] p-4 rounded-lg border border-blue-200 bg-blue-50/30 font-mono text-[13px] leading-relaxed text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-300 resize-y"
                    placeholder="Markdown 内容..." />
                ) : (
                  <div ref={editorRef}
                    className="doc-editor-content doc-readonly"
                    onMouseUp={handleMouseUp} />
                )}
              </div>

            {/* AI 浮动弹窗 */}
            {selectionPosition && selectedText && (
              <div className="ai-popup" style={{ left: `${selectionPosition.x}px`, top: `${selectionPosition.y}px` }}>
                {aiPopupMode === 'quick' && (
                  <div className="ai-popup-quick">
                    <div className="ai-popup-header">
                      <span className="text-[10px] text-slate-400">AI 编辑助手</span>
                      <button onClick={closePopup} className="ai-popup-close">x</button>
                    </div>
                    <div className="ai-popup-actions">
                      <button onClick={() => handleAIEdit('润色')} className="ai-action-btn">润色</button>
                      <button onClick={() => handleAIEdit('简化')} className="ai-action-btn">简写</button>
                      <button onClick={() => handleAIEdit('扩展')} className="ai-action-btn">扩写</button>
                      <button onClick={() => handleAIEdit('专业改写')} className="ai-action-btn">改写</button>
                      <button onClick={() => handleAIEdit('翻译为英文')} className="ai-action-btn">翻译</button>
                    </div>
                    <div className="ai-popup-footer">
                      <button onClick={() => { setAiPopupMode('custom'); setCustomInstruction('') }} className="ai-custom-trigger">自定义指令</button>
                    </div>
                  </div>
                )}
                {aiPopupMode === 'custom' && (
                  <div className="ai-popup-custom">
                    <div className="ai-popup-header">
                      <span className="text-[10px] text-slate-400">自定义编辑指令</span>
                      <button onClick={closePopup} className="ai-popup-close">x</button>
                    </div>
                    <div className="flex gap-1.5 p-2">
                      <input type="text" value={customInstruction} onChange={e => setCustomInstruction(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter' && customInstruction.trim()) handleAIEdit(customInstruction.trim()) }}
                        placeholder="描述你想要的修改..." autoFocus
                        className="flex-1 px-2.5 py-1.5 text-[11px] border border-slate-200 rounded-md focus:outline-none focus:border-blue-400" />
                      <button onClick={() => customInstruction.trim() && handleAIEdit(customInstruction.trim())}
                        className="px-3 py-1.5 text-[11px] bg-blue-500 text-white rounded-md hover:bg-blue-600 disabled:opacity-50 transition-all"
                        disabled={!customInstruction.trim() || isEditingAI}>{isEditingAI ? '...' : '执行'}</button>
                    </div>
                  </div>
                )}
                {aiPopupMode === 'result' && aiResult && (
                  <div className="ai-popup-result">
                    <div className="ai-popup-header">
                      <span className="text-[10px] text-purple-500 font-medium">AI 修改建议</span>
                      <button onClick={closePopup} className="ai-popup-close">x</button>
                    </div>
                    <div className="p-2.5 space-y-2">
                      <div className="p-2 bg-red-50 rounded-md border border-red-100">
                        <div className="text-[9px] text-red-400 mb-1 font-medium">原文</div>
                        <div className="text-[11px] text-red-700 line-through">{selectedText}</div>
                      </div>
                      <div className="p-2 bg-green-50 rounded-md border border-green-100">
                        <div className="text-[9px] text-green-500 mb-1 font-medium">改写后</div>
                        <div className="text-[11px] text-green-800 font-medium">{aiResult}</div>
                      </div>
                    </div>
                    <div className="flex gap-1.5 p-2 pt-0">
                      <button onClick={handleAccept} className="flex-1 py-1.5 text-[11px] bg-blue-500 text-white rounded-md hover:bg-blue-600 transition-all font-medium">采纳</button>
                      <button onClick={() => { setAiResult(''); setAiPopupMode('quick') }} className="flex-1 py-1.5 text-[11px] bg-white text-slate-600 border border-slate-200 rounded-md hover:bg-slate-50 transition-all">拒绝</button>
                    </div>
                  </div>
                )}
                {aiPopupMode === 'result' && !aiResult && isEditingAI && (
                  <div className="ai-popup-result">
                    <div className="p-3 flex items-center gap-2">
                      <div className="animate-spin h-4 w-4 border-2 border-purple-500 border-t-transparent rounded-full" />
                      <span className="text-[11px] text-slate-500">AI 正在改写...</span>
                    </div>
                  </div>
                )}
              </div>
            )}
            </div>

          {/* 释义管理侧面板 */}
          {showDefPanel && (
            <div className="w-80 border-l border-slate-200 bg-white overflow-y-auto shrink-0">
              <div className="sticky top-0 bg-white border-b border-slate-100 px-3 py-2.5 flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-700">释义管理</span>
                <button onClick={() => { setShowDefPanel(false); setEditingDefIdx(null) }}
                  className="text-[10px] text-slate-400 hover:text-red-500 transition-colors">关闭</button>
              </div>
              <div className="p-3 space-y-2">
                {getDefItems().map((def, idx) => (
                  <div key={idx} className="rounded-lg border border-slate-200 bg-slate-50/50 overflow-hidden">
                    <div className="px-3 py-2 border-b border-slate-100 flex items-center justify-between">
                      <span className="text-[11px] font-bold text-blue-600">{def.term}</span>
                      <div className="flex gap-1">
                        <button onClick={() => { setEditingDefIdx(idx); setEditingDefText(def.fullLine) }}
                          className="text-[9px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-500 hover:bg-blue-100 border border-blue-200 transition-all">
                          {editingDefIdx === idx ? '取消' : '编辑'}
                        </button>
                        <button onClick={() => deleteDefItem(idx)}
                          className="text-[9px] px-1.5 py-0.5 rounded bg-red-50 text-red-500 hover:bg-red-100 border border-red-200 transition-all">
                          删除
                        </button>
                      </div>
                    </div>
                    {editingDefIdx === idx ? (
                      <div className="p-2">
                        <textarea value={editingDefText}
                          onChange={e => setEditingDefText(e.target.value)}
                          className="w-full text-[11px] p-2 rounded border border-blue-200 bg-white font-mono leading-relaxed resize-y min-h-[60px] focus:outline-none focus:ring-1 focus:ring-blue-300" />
                        <button onClick={() => saveDefEdit(idx, editingDefText)}
                          className="mt-1 w-full py-1 text-[10px] bg-blue-500 text-white rounded hover:bg-blue-600 transition-all font-medium">
                          保存
                        </button>
                      </div>
                    ) : (
                      <div className="px-3 py-2">
                        <p className="text-[11px] text-slate-600 leading-relaxed line-clamp-4">{def.content}</p>
                        {def.source && (
                          <p className="text-[9px] text-blue-400 mt-1 truncate" title={def.source}>来源：{def.source}</p>
                        )}
                      </div>
                    )}
                  </div>
                ))}
                {getDefItems().length === 0 && (
                  <p className="text-[11px] text-slate-400 text-center py-6">暂无释义条目</p>
                )}
              </div>
            </div>
          )}
          </div>
        </div>
      )}
    </div>
  )
}
