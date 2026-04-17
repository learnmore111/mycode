import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'

interface Props {
  content: string
}

export default function TextContent({ content }: Props) {
  if (!content) return null

  return (
    <div className="prose prose-sm max-w-none break-words
      prose-p:my-1.5 prose-p:leading-relaxed
      prose-headings:my-3 prose-headings:text-ink-strong prose-headings:font-semibold prose-headings:tracking-tight
      prose-pre:my-3 prose-pre:bg-surface-2 prose-pre:border prose-pre:border-line
      prose-pre:rounded-lg
      prose-code:text-accent prose-code:bg-surface-2 prose-code:border prose-code:border-line
      prose-code:rounded prose-code:px-1.5 prose-code:py-0.5 prose-code:text-[0.85em] prose-code:font-mono prose-code:font-medium
      prose-code:before:content-none prose-code:after:content-none
      prose-a:text-accent prose-a:no-underline prose-a:font-medium hover:prose-a:underline
      prose-strong:text-ink-strong prose-strong:font-semibold
      prose-table:text-sm
      prose-li:my-0.5
      prose-blockquote:border-accent/30 prose-blockquote:text-ink-secondary prose-blockquote:not-italic
    ">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
