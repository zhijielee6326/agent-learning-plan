export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
}

export interface Product {
  id: string
  product_name: string
  category: string
  sub_category: string
  version: string
  filing_no: string
}

export interface TocItem {
  level: number
  text: string
  id: string
}

export interface ChatSession {
  id: string
  title: string
  messages: Message[]
  editorContent: string
  sources: string[]
  createdAt: number
  updatedAt: number
}
