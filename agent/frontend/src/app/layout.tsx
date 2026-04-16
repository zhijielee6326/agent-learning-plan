import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: '保险产品精算智能体',
  description: '保险公司内部产品条款检索、生成与溯源系统',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">
        {children}
      </body>
    </html>
  )
}
