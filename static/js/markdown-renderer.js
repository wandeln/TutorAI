/**
 * Markdown + LaTeX Renderer für das Tutor-System.
 *
 * Verwendet:
 * - marked.js (MIT) für Markdown-Parser
 * - KaTeX (MIT) für LaTeX-Rendering
 * - highlight.js (BSD-3) für Python-Syntax-Highlighting
 *
 * Inline-Latex:  $...$        → Inline
 * Display-Latex: $$...$$      → Block
 *
 * usage:
 *   renderMarkdown(text, element)
 *   renderMarkdown(text, element, { preview: true })  // Editor-Preview
 */

function renderMarkdown(text, targetElement, options = {}) {
  if (!text || typeof text !== 'string') {
    targetElement.innerHTML = '';
    return;
  }

  const { preview = false } = options;

  // 1. Extract $$...$$ blocks and replace with placeholders
  const latexBlocks = [];
  let processed = text.replace(/\$\$([\s\S]*?)\$\$/g, (match, latex) => {
    const idx = latexBlocks.length;
    latexBlocks.push(latex.trim());
    return `%%LATEX_BLOCK_${idx}%%`;
  });

  // 2. Render Markdown (marked)
  const mdHtml = marked.parse(processed, {
    breaks: true,
    gfm: true,
    headerIds: false,
    mangle: false,
  });

  // 3. Restore LaTeX blocks
  let html = mdHtml;
  latexBlocks.forEach((latex, idx) => {
    html = html.replace(`%%LATEX_BLOCK_${idx}%%`, renderLatexBlock(latex));
  });

  // 4. Process inline $...$ LaTeX in the final HTML
  html = processInlineLatex(html);

  // 5. Apply syntax highlighting to code blocks
  html = highlightCodeBlocks(html);

  // 6. Decode HTML entities in non-code text — marked escapes < and > to &lt;/&gt;
  //    which is correct for security but unwanted for Python REPL prompts (>>>) and
  //    comparison operators in regular prose. Code blocks are already handled above.
  html = decodeTextEntities(html);

  // Always wrap in .markdown-preview so CSS rules apply consistently.
  // In preview mode, add an extra wrapper for the editor scroll area.
  if (preview) {
    targetElement.innerHTML = `<div class="markdown-preview">${html}</div>`;
  } else {
    targetElement.innerHTML = `<div class="markdown-preview">${html}</div>`;
  }
}

function renderLatexBlock(latex) {
  try {
    return katex.renderToString(latex, {
      displayMode: true,
      throwOnError: false,
      trust: true,
    });
  } catch (e) {
    return `<pre class="text-red-500 bg-red-50 p-2 rounded">KaTeX Error: ${e.message}</pre>`;
  }
}

function renderLatexInline(latex) {
  try {
    return katex.renderToString(latex, {
      displayMode: false,
      throwOnError: false,
      trust: true,
    });
  } catch (e) {
    return `<span class="text-red-500">\\(${latex}\\)</span>`;
  }
}

function processInlineLatex(html) {
  // Process $...$ that appear outside of HTML tags and code blocks.
  // marked has already escaped < and > to &lt;/&gt; in the text, so we
  // decode them _before_ handing the LaTeX to KaTeX.
  const parts = html.split(/(<[^>]*>)/g);
  return parts.map((part, i) => {
    if (i % 2 === 0 && part.includes('$')) {
      return part.replace(/\$([^\$]+?)\$/g, (match, latex) => {
        // Decode entities that marked escaped inside the LaTeX expression
        const decoded = latex.trim()
          .replace(/&lt;/g, '<')
          .replace(/&gt;/g, '>')
          .replace(/&amp;/g, '&');
        return renderLatexInline(decoded);
      });
    }
    return part;
  }).join('');
}

function highlightCodeBlocks(html) {
  // Find fenced code blocks: ```python ... ``` or ``` ... ```
  // marked renders them as <pre><code>...</code></pre> or <pre><code class="language-...">...</code></pre>
  return html.replace(/<pre><code([^>]*)>([\s\S]*?)<\/code><\/pre>/g, (match, attrs, code) => {
    // Decode HTML entities that marked escaped in code content (> → >, < → <, etc.)
    const decoded = code
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&amp;/g, '&')
      .replace(/&#39;/g, "'")
      .replace(/&quot;/g, '"');

    const isPython = attrs.includes('python') || !attrs.trim();

    if (isPython && typeof hljs !== 'undefined') {
      try {
        const highlighted = hljs.highlight(decoded.trim(), { language: 'python' }).value;
        return `<pre><code class="language-python">${highlighted}</code></pre>`;
      } catch (e) {
        // Fallback: re-escape and return as-is
      }
    }

    // Escape HTML entities in code for non-highlighted blocks
    const escaped = decoded
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    return `<pre><code${attrs}>${escaped}</code></pre>`;
  });
}

function decodeTextEntities(html) {
  // Decode &lt; and &gt; in text nodes only, leaving real HTML tags intact.
  // Splits on HTML tags and <pre> blocks so we only touch text content
  // (even-indexed parts after the split).
  const parts = html.split(/(<pre>[\s\S]*?<\/pre>|<span class="katex(?:-display)?">[\s\S]*?<\/span>|<[^>]*>)/g);
  return parts.map((part, i) => {
    if (i % 2 === 0) {
      // Text node — decode escaped angle brackets
      return part.replace(/&lt;/g, '<').replace(/&gt;/g, '>');
    }
    // HTML tag / pre block / KaTeX output — leave as-is
    return part;
  }).join('');
}

/**
 * Create a Markdown editor with a toggle between edit and preview mode.
 *
 * Appends a toggle button next to the header in the given container,
 * and a preview div after the textarea.
 *
 * @param {string} containerId - The ID of the container element that holds the textarea
 * @param {Object} options - { headerSelector?: string, onValueChange?: (val) => void }
 * @returns {Object} - { textarea, previewDiv, toggleBtn, isPreview() }
 */
function createMarkdownEditor(containerId, options = {}) {
  const container = document.getElementById(containerId);
  if (!container) {
    console.warn(`createMarkdownEditor: Container #${containerId} not found`);
    return null;
  }

  const textarea = (options.textareaId
    ? (document.getElementById(options.textareaId) || null)
    : container.querySelector('textarea'));
  if (!textarea) {
    console.warn(`createMarkdownEditor: No textarea found in #${containerId}`);
    return null;
  }

  // Create preview div
  const previewDiv = document.createElement('div');
  previewDiv.className = 'markdown-preview-area hidden min-h-[200px] border border-gray-300 rounded-lg p-4 bg-white overflow-y-auto';

  // Create toggle button
  const toggleBtn = document.createElement('button');
  toggleBtn.type = 'button';
  toggleBtn.className = 'text-gray-500 hover:text-gray-700 text-sm px-3 py-1.5 rounded-lg border border-gray-300 hover:bg-gray-50 transition inline-flex items-center gap-1.5';
  toggleBtn.innerHTML = '<span>👁️</span> <span>Preview</span>';

  // Insert preview div after textarea
  textarea.parentNode.insertBefore(previewDiv, textarea.nextSibling);

  // Insert toggle button
  //   — if anchor given: append *into* the anchor element (e.g. a toolbar flex container)
  //   — otherwise: insert after the textarea (standalone, needs margin-top spacing)
  const anchorId = options.buttonAnchor;
  if (anchorId) {
    const anchorEl = document.getElementById(anchorId);
    if (anchorEl) {
      anchorEl.appendChild(toggleBtn);
    } else {
      toggleBtn.style.marginTop = '0.5rem';
      textarea.parentNode.insertBefore(toggleBtn, textarea.nextSibling);
    }
  } else {
    // Fallback: insert after textarea
    toggleBtn.style.marginTop = '0.5rem';
    textarea.parentNode.insertBefore(toggleBtn, textarea.nextSibling);
  }

  let isPreview = false;

  toggleBtn.addEventListener('click', () => {
    isPreview = !isPreview;

    if (isPreview) {
      textarea.classList.add('hidden');
      previewDiv.classList.remove('hidden');
      toggleBtn.innerHTML = '<span>✏️</span> <span>Edit</span>';
      toggleBtn.classList.add('bg-blue-50', 'border-blue-300', 'text-blue-700');
      renderMarkdown(textarea.value, previewDiv, { preview: true });
    } else {
      previewDiv.classList.add('hidden');
      textarea.classList.remove('hidden');
      toggleBtn.innerHTML = '<span>👁️</span> <span>Preview</span>';
      toggleBtn.classList.remove('bg-blue-50', 'border-blue-300', 'text-blue-700');
    }
  });

  // Auto-update preview while typing (debounced)
  let updateTimeout = null;
  textarea.addEventListener('input', () => {
    if (isPreview) {
      clearTimeout(updateTimeout);
      updateTimeout = setTimeout(() => {
        renderMarkdown(textarea.value, previewDiv, { preview: true });
      }, 300);
    }
    if (options.onValueChange) {
      options.onValueChange(textarea.value);
    }
  });

  return { textarea, previewDiv, toggleBtn, isPreview: () => isPreview };
}