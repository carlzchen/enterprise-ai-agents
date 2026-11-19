/** 轻量 Markdown 渲染（先转义 HTML，防 XSS）：标题、表格、代码块、列表、粗体、行内代码、图片链接 */
export const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

function tables(h) {
  return h.replace(/(^\|.*\|\s*$\n?)+/gm, block => {
    const rows = block.trim().split('\n').filter(r => !/^\|[\s:|-]+\|$/.test(r.trim()))
    const cells = r => r.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim())
    const [head, ...body] = rows
    return '<table class="md-table"><tr>' + cells(head).map(c => `<th>${c}</th>`).join('') + '</tr>' +
      body.map(r => '<tr>' + cells(r).map(c => `<td>${c}</td>`).join('') + '</tr>').join('') + '</table>\n'
  })
}

export function renderMarkdown(text) {
  const blocks = []
  let h = esc(text).replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => { blocks.push(`<pre class="md-code">${code}</pre>`); return `@@CODE${blocks.length - 1}@@` })
  h = tables(h)
  h = h.replace(/!\[([^\]]*)\]\((https?:[^)\s]+)\)/g, '<img src="$2" alt="$1" class="inline-img">')
  h = h.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>')
  h = h.replace(/^# (.*)$/gm, '<h2>$1</h2>').replace(/^## (.*)$/gm, '<h3>$1</h3>').replace(/^### (.*)$/gm, '<h4>$1</h4>')
  h = h.replace(/^\s*[-*] (.*)$/gm, '<li>$1</li>').replace(/^\s*\d+\. (.*)$/gm, '<li>$1</li>')
  h = h.replace(/\n/g, '<br>').replace(/(<\/(table|h2|h3|h4|li)>)<br>/g, '$1')
  return h.replace(/@@CODE(\d+)@@/g, (_, i) => blocks[Number(i)])
}
