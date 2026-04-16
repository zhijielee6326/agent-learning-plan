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

  // 目录
  const [tocItems, setTocItems] = useState<TocItem[]>([])
  const [showToc, setShowToc] = useState(true)

  // 编辑器（默认只读展示模式，点击编辑按钮才开启）
  const [isEditing, setIsEditing] = useState(false)
  const [isEditingAI, setIsEditingAI] = useState(false)

  // AI 弹窗
  const [selectedText, setSelectedText] = useState('')
  const [selectionPosition, setSelectionPosition] = useState<{ x: number; y: number } | null>(null)
  const [aiPopupMode, setAiPopupMode] = useState<'quick' | 'custom' | 'result'>('quick')
  const [customInstruction, setCustomInstruction] = useState('')
  const [aiResult, setAiResult] = useState('')

  // Refs
  const chatEndRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const editorRef = useRef<HTMLDivElement>(null)
  const lastRenderedRef = useRef('')

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
    setEditorContent(DEFAULT_EDITOR_CONTENT)
    setSources([])
    lastRenderedRef.current = ''
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
      remaining.push(sessions[0]) // will be overwritten
    } else if (id === activeSessionId) {
      switchSession(remaining[0].id)
    }
    const final = sessions.filter(s => s.id !== id)
    setSessions(final)
    saveToStorage(final)
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
  }, [editorContent, isStreaming, isEditing])

  // 编辑器退出编辑态时，从 DOM 同步内容并重新渲染
  const exitEditMode = useCallback(() => {
    setIsEditing(false)
    if (editorRef.current) {
      const md = editorRef.current.innerText || ''
      lastRenderedRef.current = ''  // 强制重新渲染
      if (md !== editorContent) setEditorContent(md)
    }
  }, [editorContent])

  useEffect(() => { fetch(`${API_BASE}/products`).then(r => r.json()).then(d => setProducts(d.products || [])).catch(() => {}) }, [])
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  // ========== 聊天 ==========
  const sendMessageStream = useCallback(async (query: string) => {
    setIsLoading(true)
    setIsStreaming(true)
    let fullContent = ''
    let collectedSources: string[] = []
    const aid = Date.now().toString() + '-a'
    setMessages(prev => [...prev, { id: aid, role: 'assistant', content: '' }])
    const ac = new AbortController()
    abortRef.current = ac

    // 打字机效果：用 requestAnimationFrame 匀速输出，只更新聊天面板
    streamBufferRef.current = ''
    displayedLenRef.current = 0
    typingMsgIdRef.current = aid
    const CHARS_PER_FRAME = 3  // 每帧输出3个字符，60fps下约180字/秒

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
            }
            else if (ev.type === 'sources') collectedSources = ev.sources || []
            else if (ev.type === 'chapter_start') setClauseProgress(`正在生成：${ev.chapter} (${ev.index + 1}/${ev.total})`)
            else if (ev.type === 'progress') setClauseProgress(ev.message || '')
            else if (ev.type === 'error') { fullContent += (fullContent ? '\n\n' : '') + `> ⚠️ ${ev.message || ev.content || '服务调用失败'}`; streamBufferRef.current = fullContent; setClauseProgress('') }
            else if (ev.type === 'done') { setClauseProgress(''); if (ev.source_products) collectedSources = ev.source_products }
            if (collectedSources.length > 0) setSources(collectedSources)
          } catch {}
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') fullContent = fullContent || '（对话已取消）'
      else fullContent = '连接后端服务失败，请确认后端服务已启动（python main.py）'
      streamBufferRef.current = fullContent
    } finally {
      // 流结束：停止打字机，一次性显示全部内容
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
    }
  }, [activeSessionId])

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
      // 使用视口坐标（fixed定位），弹窗出现在选区上方
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
      const updated = prev.replace(selectedText, aiResult)
      return updated
    })
    // 强制重新渲染编辑器
    lastRenderedRef.current = ''
    setSelectedText(''); setAiResult(''); setSelectionPosition(null); setAiPopupMode('quick')
    window.getSelection()?.removeAllRanges()
  }, [selectedText, aiResult])

  const closePopup = useCallback(() => {
    setSelectionPosition(null); setSelectedText(''); setAiResult(''); setAiPopupMode('quick')
    window.getSelection()?.removeAllRanges()
  }, [])

  // ========== 导出 ==========
  const exportMD = () => {
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([editorContent], { type: 'text/markdown;charset=utf-8' }))
    a.download = '保险条款分析.md'; a.click()
  }
  const exportWord = async () => {
    try {
      const res = await fetch(`${API_BASE}/export/word`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: editorContent, title: '保险产品条款' }) })
      if (!res.ok) throw new Error()
      const a = document.createElement('a'); a.href = URL.createObjectURL(await res.blob()); a.download = '保险产品条款.docx'; a.click()
    } catch { alert('Word导出失败') }
  }

  const wordCount = editorContent.length

  return (
    <div className="flex h-screen bg-white">
      {/* ========== 左侧：对话面板 ========== */}
      <div className="w-[380px] flex flex-col border-r border-slate-200 bg-slate-50 shrink-0">
        {/* 头部 */}
        <div className="px-4 py-3 border-b border-slate-200 bg-white">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white text-xs font-bold shadow-sm">AI</div>
            <div className="flex-1 min-w-0">
              <h1 className="text-sm font-bold text-slate-800">保险产品精算智能体</h1>
<p className="text-[10px] text-slate-400">GLM-5.1 · RAG检索 · 条款溯源</p>
            </div>
            <div className="flex gap-1">
              <button onClick={() => setShowSessionList(!showSessionList)}
                className="px-2 py-1 text-[10px] rounded-md bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 transition-all">
                📂 历史({sessions.length})
              </button>
              <button onClick={createNewSession}
                className="px-2 py-1 text-[10px] rounded-md bg-blue-50 text-blue-600 hover:bg-blue-100 border border-blue-200 transition-all">
                ＋新建
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
                <span className="text-[11px]">📄</span>
                <span className="flex-1 text-[11px] text-slate-700 truncate">{s.title}</span>
                <span className="text-[9px] text-slate-300">{new Date(s.updatedAt).toLocaleDateString()}</span>
                <button onClick={(e) => deleteSession(s.id, e)}
                  className="text-[10px] text-slate-300 hover:text-red-400 ml-1">✕</button>
              </div>
            ))}
          </div>
        )}

        {/* 消息列表 */}
        <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
          {messages.length === 0 && (
            <div className="space-y-2">
              <p className="text-center text-slate-400 text-xs py-3">选择问题开始对话</p>
              {QUICK_QUERIES.map((q, i) => (
                <button key={i} onClick={() => setInput(q.text)}
                  className="w-full text-left px-3 py-2.5 rounded-lg bg-white border border-slate-200 text-xs text-slate-600 hover:border-blue-300 hover:bg-blue-50 transition-all">
                  <span className="mr-1.5">{q.icon}</span>{q.text}
                </button>
              ))}
            </div>
          )}
          {messages.map(msg => (
            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[88%] px-3 py-2 text-xs leading-relaxed ${msg.role === 'user' ? 'msg-user' : 'msg-assistant'}`}>
                {msg.role === 'assistant' ? (
                  <div className="markdown-content">
                    <SimpleMarkdown content={msg.content} />
                    {isStreaming && msg.id === messages[messages.length - 1]?.id && <span className="cursor-blink" />}
                  </div>
                ) : msg.content}
              </div>
            </div>
          ))}
          {isLoading && !isStreaming && (
            <div className="flex justify-start">
              <div className="msg-assistant px-3 py-2 text-xs text-slate-400 flex items-center gap-1.5">
                <div className="animate-spin h-3 w-3 border-2 border-blue-500 border-t-transparent rounded-full" />检索中...
              </div>
            </div>
          )}
          <div ref={chatEndRef} />
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
        <div className="px-3 py-2.5 border-t border-slate-200 bg-white">
          <div className="flex gap-1.5">
            <textarea value={input} onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() } }}
              placeholder="输入您的保险条款问题..." rows={2}
              className="flex-1 resize-none rounded-lg border border-slate-200 px-3 py-2 text-xs focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100 transition-all" />
            {isStreaming ? (
              <button onClick={() => abortRef.current?.abort()} className="px-3 rounded-lg text-xs font-medium bg-red-500 text-white hover:bg-red-600 self-end transition-all">停止</button>
            ) : (
              <button onClick={handleSend} disabled={isLoading || !input.trim()}
                className="btn-primary px-3 rounded-lg text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed self-end">发送</button>
            )}
          </div>
        </div>
      </div>

      {/* ========== 右侧：编辑区 ========== */}
      <div className="flex-1 flex flex-col">
        {/* 顶部工具栏 */}
        <div className="h-11 px-4 border-b border-slate-200 bg-white flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2">
            <button onClick={() => setShowToc(!showToc)}
              className="px-2 py-1 text-[11px] rounded-md bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 transition-all">
              📑 {showToc ? '收起目录' : '展开目录'}
            </button>
            <span className="text-[10px] text-slate-300">{wordCount} 字</span>
            {sources.length > 0 && <span className="text-[10px] text-blue-400">引用 {sources.length} 条</span>}
            <span className="text-[10px] text-slate-300">产品库 {products.length} 款</span>
          </div>
          <div className="flex gap-1.5">
            {isEditing ? (
              <button onClick={exitEditMode}
                className="px-2 py-1 text-[11px] rounded-md bg-green-50 text-green-600 hover:bg-green-100 border border-green-200 transition-all font-medium">
                ✓ 完成编辑
              </button>
            ) : (
              <button onClick={() => { setIsEditing(true); lastRenderedRef.current = '' }}
                className="px-2 py-1 text-[11px] rounded-md bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 transition-all">
                ✏️ 编辑
              </button>
            )}
            <button onClick={exportMD} className="px-2 py-1 text-[11px] rounded-md bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 transition-all">导出 Markdown</button>
            <button onClick={exportWord} className="px-2 py-1 text-[11px] rounded-md bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 transition-all">导出 Word</button>
          </div>
        </div>

        {/* 编辑区主体 */}
        <div className="flex-1 flex overflow-hidden">
          {/* 目录大纲 */}
          {showToc && tocItems.length > 0 && (
            <div className="w-48 border-r border-slate-100 bg-slate-50/50 overflow-y-auto py-3 px-2 shrink-0">
              <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2 px-2">目录大纲</div>
              {tocItems.map((item, i) => (
                <button key={i} onClick={() => document.getElementById(item.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
                  className={`w-full text-left px-2 py-1 rounded-md text-[11px] transition-all hover:bg-blue-50 hover:text-blue-600 mb-0.5 ${
                    item.level === 2 ? 'font-medium text-slate-700' : item.level === 3 ? 'pl-5 text-slate-500' : 'pl-8 text-slate-400'
                  }`}>
                  {item.text}
                </button>
              ))}
            </div>
          )}

          {/* 文档编辑区 */}
          <div className="flex-1 overflow-y-auto relative">
            <div className="max-w-3xl mx-auto px-10 py-8">
              <div ref={editorRef}
                className={`doc-editor-content ${isEditing ? 'doc-editing' : 'doc-readonly'}`}
                contentEditable={isEditing}
                suppressContentEditableWarning
                onMouseUp={handleMouseUp} />
            </div>

            {/* AI 浮动弹窗 - 使用 fixed 定位覆盖在文字上方 */}
            {selectionPosition && selectedText && (
              <div className="ai-popup" style={{ left: `${selectionPosition.x}px`, top: `${selectionPosition.y}px` }}>
                {aiPopupMode === 'quick' && (
                  <div className="ai-popup-quick">
                    <div className="ai-popup-header">
                      <span className="text-[10px] text-slate-400">AI 编辑助手</span>
                      <button onClick={closePopup} className="ai-popup-close">✕</button>
                    </div>
                    <div className="ai-popup-actions">
                      <button onClick={() => handleAIEdit('润色')} className="ai-action-btn">✨ 润色</button>
                      <button onClick={() => handleAIEdit('简化')} className="ai-action-btn">✂️ 简写</button>
                      <button onClick={() => handleAIEdit('扩展')} className="ai-action-btn">📝 扩写</button>
                      <button onClick={() => handleAIEdit('专业改写')} className="ai-action-btn">👔 改写</button>
                      <button onClick={() => handleAIEdit('翻译为英文')} className="ai-action-btn">🌐 翻译</button>
                    </div>
                    <div className="ai-popup-footer">
                      <button onClick={() => { setAiPopupMode('custom'); setCustomInstruction('') }} className="ai-custom-trigger">✏️ 自定义指令</button>
                    </div>
                  </div>
                )}
                {aiPopupMode === 'custom' && (
                  <div className="ai-popup-custom">
                    <div className="ai-popup-header">
                      <span className="text-[10px] text-slate-400">自定义编辑指令</span>
                      <button onClick={closePopup} className="ai-popup-close">✕</button>
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
                      <span className="text-[10px] text-purple-500 font-medium">🤖 AI 修改建议</span>
                      <button onClick={closePopup} className="ai-popup-close">✕</button>
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
                      <button onClick={handleAccept} className="flex-1 py-1.5 text-[11px] bg-blue-500 text-white rounded-md hover:bg-blue-600 transition-all font-medium">✓ 采纳</button>
                      <button onClick={() => { setAiResult(''); setAiPopupMode('quick') }} className="flex-1 py-1.5 text-[11px] bg-white text-slate-600 border border-slate-200 rounded-md hover:bg-slate-50 transition-all">✕ 拒绝</button>
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
        </div>

      </div>
    </div>
  )
}

// ========== 内联 Markdown 解析 ==========
function parseInline(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = []
  // 匹配 **bold**、*italic*、`code`、[link text]
  const regex = /(\*\*(.+?)\*\*)|(\*(.+?)\*)|(`(.+?)`)|(\[(.+?)\])/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  let keyIdx = 0
  while ((match = regex.exec(text)) !== null) {
    // 添加匹配前的普通文本
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index))
    }
    if (match[1]) {
      // **bold**
      nodes.push(<strong key={keyIdx++}>{match[2]}</strong>)
    } else if (match[3]) {
      // *italic*
      nodes.push(<em key={keyIdx++}>{match[4]}</em>)
    } else if (match[5]) {
      // `code`
      nodes.push(<code key={keyIdx++} className="bg-slate-100 text-blue-600 px-1 py-0.5 rounded text-[10px] font-mono">{match[6]}</code>)
    } else if (match[7]) {
      // [溯源标注]
      nodes.push(<span key={keyIdx++} className="text-blue-500 text-[10px] bg-blue-50 px-1 rounded">{match[8]}</span>)
    }
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex))
  }
  return nodes.length > 0 ? nodes : [text]
}

// ========== 简易 Markdown 组件 ==========
function SimpleMarkdown({ content }: { content: string }) {
  if (!content) return null
  return (
    <>
      {content.split('\n').map((line, i) => {
        if (line.startsWith('## ')) return <h2 key={i} className="text-sm font-bold text-slate-800 mt-2 mb-1">{parseInline(line.slice(3))}</h2>
        if (line.startsWith('### ')) return <h3 key={i} className="text-xs font-semibold text-slate-700 mt-1.5">{parseInline(line.slice(4))}</h3>
        if (line.startsWith('- ')) return <p key={i} className="pl-3">• {parseInline(line.slice(2))}</p>
        if (line.startsWith('> ')) return <blockquote key={i} className="border-l-2 border-blue-300 pl-2 text-blue-600 my-1">{parseInline(line.slice(2))}</blockquote>
        if (line === '---') return <hr key={i} className="border-slate-200 my-2" />
        if (/^\d+\.\s/.test(line)) return <p key={i} className="pl-3">{parseInline(line)}</p>
        if (line.trim()) return <p key={i}>{parseInline(line)}</p>
        return null
      })}
    </>
  )
}
