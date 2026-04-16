import { TocItem } from './types'

function esc(t: string): string {
  return t.replace(/&/g, '&').replace(/</g, '<').replace(/>/g, '>').replace(/"/g, '"')
}

function inline(t: string): string {
  let s = esc(t)
  // **bold**
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  // 溯源标注 [产品名 > 章节名 > 条款] 或 [产品名 > 章节名]
  s = s.replace(/\[([^\]]+?[>＞][^\]]+?)\]/g, '<span class="source-tag">$1</span>')
  return s
}

export function parseToc(md: string): TocItem[] {
  const items: TocItem[] = []
  const regex = /^(#{1,6})\s+(.+)$/gm
  let match
  while ((match = regex.exec(md)) !== null) {
    items.push({ level: match[1].length, text: match[2].replace(/\*\*/g, ''), id: `heading-${items.length}` })
  }
  return items
}

export function markdownToHTML(md: string): string {
  if (!md) return ''
  
  const lines = md.split('\n')
  const html: string[] = []
  let i = 0
  let blockIdx = 0
  let headingIdx = 0  // 独立的标题计数器，与 parseToc 保持一致
  let inList = false
  let listType = ''

  function closeList() {
    if (inList) {
      html.push(listType === 'ol' ? '</ol>' : '</ul>')
      inList = false
      listType = ''
    }
  }

  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trimEnd()

    // 空行
    if (!trimmed) {
      closeList()
      i++
      continue
    }

    // H1
    if (trimmed.startsWith('# ') && !trimmed.startsWith('## ')) {
      closeList()
      html.push(`<h1 class="doc-h1" id="heading-${headingIdx}" data-block="${blockIdx}">${inline(trimmed.slice(2))}</h1>`)
      headingIdx++; blockIdx++; i++; continue
    }

    // H2
    if (trimmed.startsWith('## ') && !trimmed.startsWith('### ')) {
      closeList()
      html.push(`<h2 class="doc-h2" id="heading-${headingIdx}" data-block="${blockIdx}">${inline(trimmed.slice(3))}</h2>`)
      headingIdx++; blockIdx++; i++; continue
    }

    // H3
    if (trimmed.startsWith('### ') && !trimmed.startsWith('#### ')) {
      closeList()
      html.push(`<h3 class="doc-h3" id="heading-${headingIdx}" data-block="${blockIdx}">${inline(trimmed.slice(4))}</h3>`)
      headingIdx++; blockIdx++; i++; continue
    }

    // H4
    if (trimmed.startsWith('#### ')) {
      closeList()
      html.push(`<h4 class="doc-h4" id="heading-${headingIdx}" data-block="${blockIdx}">${inline(trimmed.slice(5))}</h4>`)
      headingIdx++; blockIdx++; i++; continue
    }

    // 水平线
    if (trimmed === '---' || trimmed === '***' || trimmed === '___') {
      closeList()
      html.push(`<hr data-block="${blockIdx}" />`)
      blockIdx++; i++; continue
    }

    // 引用块
    if (trimmed.startsWith('> ')) {
      closeList()
      html.push(`<blockquote class="doc-quote" data-block="${blockIdx}"><p>${inline(trimmed.slice(2))}</p></blockquote>`)
      blockIdx++; i++; continue
    }

    // 有序列表
    if (/^\d+[\.、]\s/.test(trimmed)) {
      if (!inList || listType !== 'ol') {
        closeList()
        html.push(`<ol class="doc-ol" data-block="${blockIdx}">`)
        inList = true
        listType = 'ol'
      }
      html.push(`<li>${inline(trimmed.replace(/^\d+[\.、]\s/, ''))}</li>`)
      blockIdx++; i++; continue
    }

    // 无序列表
    if (trimmed.startsWith('- ') || trimmed.startsWith('• ')) {
      if (!inList || listType !== 'ul') {
        closeList()
        html.push(`<ul class="doc-ul" data-block="${blockIdx}">`)
        inList = true
        listType = 'ul'
      }
      html.push(`<li>${inline(trimmed.slice(2))}</li>`)
      blockIdx++; i++; continue
    }

    // 普通段落（合并连续行）
    closeList()
    let paraLines = trimmed
    while (i + 1 < lines.length) {
      const next = lines[i + 1].trimEnd()
      if (!next || next.startsWith('#') || next.startsWith('>') || next === '---' ||
          next.startsWith('- ') || /^\d+[\.、]/.test(next)) break
      i++
      paraLines += '\n' + next
    }
    
    // 检测条款条目模式 "第X条" - 给予特殊样式
    if (/^第[一二三四五六七八九十百零\d]+条/.test(trimmed)) {
      html.push(`<p class="doc-article" data-block="${blockIdx}">${inline(paraLines)}</p>`)
    } else {
      html.push(`<p data-block="${blockIdx}">${inline(paraLines)}</p>`)
    }
    blockIdx++; i++; continue
  }

  closeList()
  return html.join('\n')
}