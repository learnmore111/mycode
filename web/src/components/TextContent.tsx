import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'

interface Props {
  content: string
}

export default function TextContent({ content }: Props) {
  if (!content) return null

  return (
    <div className="prose prose-invert prose-sm max-w-none break-words
      prose-p:my-1.5 prose-headings:my-3 prose-headings:text-text-primary
      prose-pre:my-3 prose-pre:bg-[#0d0f14] prose-pre:border prose-pre:border-border-subtle
      prose-pre:rounded-lg
      prose-code:text-accent-blue prose-code:bg-surface-2 prose-code:rounded
      prose-code:px-1.5 prose-code:py-0.5 prose-code:text-[0.9em]
      prose-code:before:content-none prose-code:after:content-none
      prose-a:text-accent-blue prose-a:no-underline hover:prose-a:underline
      prose-strong:text-text-primary prose-strong:font-semibold
      prose-table:text-sm prose-th:text-left
      prose-li:my-0.5
    ">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
