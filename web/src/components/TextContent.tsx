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
      prose-p:my-1 prose-headings:my-2 prose-pre:my-2 prose-pre:bg-gray-900
      prose-code:text-blue-300 prose-code:before:content-none prose-code:after:content-none
      prose-a:text-blue-400 prose-a:no-underline hover:prose-a:underline
      prose-pre:rounded-lg prose-pre:border prose-pre:border-gray-700
      prose-table:text-sm prose-th:text-left
    ">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
