/**
 * Markdown + LaTeX + Mermaid Renderer für TutorAI.
 *
 * Verwendet:
 * - marked.js (MIT) für Markdown-Parser
 * - KaTeX (MIT) für LaTeX-Rendering
 * - Mermaid.js (MIT) für Diagramme (flowchart, sequence, class, state, etc.)
 * - highlight.js (BSD-3) für Python-Syntax-Highlighting
 *
 * Inline-Latex:  $...$        → Inline
 * Display-Latex: $$...$$      → Block
 * Mermaid:       ```mermaid   → SVG-Diagramm
 *
 * usage:
 *   await renderMarkdown(text, element)
 *   await renderMarkdown(text, element, { preview: true })  // Editor-Preview
 */

// Initialize Mermaid on first load
if (typeof mermaid !== 'undefined') {
  mermaid.initialize({
    startOnLoad: false,
    theme: 'default',
    securityLevel: 'loose',
  });
}

async function renderMarkdown(text, targetElement, options = {}) {
  if (!text || typeof text !== 'string') {
    targetElement.innerHTML = '';
    return;
  }

  const { preview = false } = options;

  // 1a. Extract ```mermaid ... ``` blocks and replace with placeholders
  const mermaidBlocks = [];
  let processed = text.replace(/```mermaid\n([\s\S]*?)```/g, (match, diagram) => {
    const idx = mermaidBlocks.length;
    mermaidBlocks.push(diagram.trim());
    return `%%MERmaid_BLOCK_${idx}%%`;
  });

  // 1b. Extract $$...$$ blocks and replace with placeholders
  const latexBlocks = [];
  processed = processed.replace(/\$\$([\s\S]*?)\$\$/g, (match, latex) => {
    const idx = latexBlocks.length;
    latexBlocks.push(latex.trim());
    return `%%LATEX_BLOCK_${idx}%%`;
  });

  // 1c. Extract $...$ inline math and replace with placeholders.
  //     This is critical: if $...$ content contains characters that look like
  //     HTML tags (e.g. <img ...>), marked will pass them through as raw HTML
  //     and they get executed via innerHTML before KaTeX ever sees them.
  //     By extracting here, marked never touches the LaTeX content.
  const latexInlines = [];
  processed = processed.replace(/\$([^$]+?)\$/g, (match, latex) => {
    const idx = latexInlines.length;
    latexInlines.push(latex.trim());
    return `%%LATEX_INLINE_${idx}%%`;
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

  // 4. Restore inline LaTeX (already extracted before marked)
  latexInlines.forEach((latex, idx) => {
    html = html.replace(`%%LATEX_INLINE_${idx}%%`, renderLatexInline(latex));
  });

  // 5. Apply syntax highlighting to code blocks
  html = highlightCodeBlocks(html);

  // 6. Decode HTML entities in non-code text — marked escapes < and > to &lt;/&gt;
  //    which is correct for security but unwanted for Python REPL prompts (>>>) and
  //    comparison operators in regular prose. Code blocks are already handled above.
  html = decodeTextEntities(html);

  // 7. Sanitize final HTML with DOMPurify — this is the critical XSS defense.
  //    marked.js allows raw HTML passthrough, so user input like
  //    <img src=x onerror=alert(1)> gets injected directly.
  //    DOMPurify strips dangerous attributes (onerror, onload, onclick, etc.)
  //    while preserving safe HTML and our KaTeX/syntax-highlighted output.
  if (typeof DOMPurify !== 'undefined') {
    html = DOMPurify.sanitize(html);
  }

  // 8. Render Mermaid diagrams (async — mermaid.render() returns a Promise)
  if (mermaidBlocks.length > 0 && typeof mermaid !== 'undefined') {
    const renderedDiagrams = await Promise.all(mermaidBlocks.map((diagram) => {
      return renderMermaid(diagram);
    }));
    renderedDiagrams.forEach((svg, idx) => {
      html = html.replace(`%%MERmaid_BLOCK_${idx}%%`, svg);
    });
  }

  // Always wrap in .markdown-preview so CSS rules apply consistently.
  // In preview mode, add an extra wrapper for the editor scroll area.
  if (preview) {
    targetElement.innerHTML = `<div class="markdown-preview">${html}</div>`;
  } else {
    targetElement.innerHTML = `<div class="markdown-preview">${html}</div>`;
  }
}

// Escape HTML special characters to prevent XSS in error messages
function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Sanitize LaTeX input to prevent HTML/JS injection.
// KaTeX 0.16.x has known issues where HTML tags in the input can end up in
// the rendered output, executing when inserted via innerHTML.
// Two attack vectors:
//   1. [HTML]...[TeX] macro blocks (trustedCommandConstructor bypass)
//   2. Raw HTML tags created by entity decoding (e.g. &lt; → <, &gt; → >)
function sanitizeLatex(latex) {
  // 1. Remove [HTML]...[TeX] macro blocks (loop for nested occurrences)
  let prev = '';
  while (prev !== latex) {
    prev = latex;
    latex = latex.replace(/\[HTML\][\s\S]*?\[TeX\]/gi, '');
  }
  latex = latex.replace(/\[HTML\]/gi, '').replace(/\[TeX\]/gi, '');

  // 2. Strip <script> and </script> tags (always dangerous in math context)
  latex = latex.replace(/<\/?script[\s>][^>]*>/gi, '');

  // 3. Strip HTML tags with attributes (most injection payloads require attributes
  //    like onerror=, src=, onload=, etc.). The regex requires an = sign,
  //    so it will NOT affect comparison operators like "x < y" or "a < b > c".
  latex = latex.replace(/<\s*[a-zA-Z][^>=]*=[^>]*>/gi, '');

  return latex;
}

function renderLatexBlock(latex) {
  try {
    return katex.renderToString(sanitizeLatex(latex), {
      displayMode: true,
      throwOnError: false,
      trust: false,
    });
  } catch (e) {
    return `<pre class="text-red-500 bg-red-50 p-2 rounded">KaTeX Error: ${escapeHtml(e.message)}</pre>`;
  }
}

function renderLatexInline(latex) {
  try {
    return katex.renderToString(sanitizeLatex(latex), {
      displayMode: false,
      throwOnError: false,
      trust: false,
    });
  } catch (e) {
    return `<span class="text-red-500">\(${escapeHtml(latex)}\)</span>`;
  }
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
  const parts = html.split(/(<pre>[\s\S]*?<\/pre>|<span class="katex(?:-display)?">[\s\S]*?<\/span>|<div class="mermaid-diagram">[\s\S]*?<\/div>|<[^>]*>)/g);
  return parts.map((part, i) => {
    if (i % 2 === 0) {
      // Text node — decode escaped angle brackets
      return part.replace(/&lt;/g, '<').replace(/&gt;/g, '>');
    }
    // HTML tag / pre block / KaTeX output / mermaid diagram — leave as-is
    return part;
  }).join('');
}

// ─── Mermaid Diagram Rendering ──────────────────────────────────────

async function renderMermaid(diagramText) {
  if (typeof mermaid === 'undefined') {
    return `<pre class="text-orange-500 bg-orange-50 p-2 rounded">Mermaid not loaded</pre>`;
  }

  try {
    const { svg } = await mermaid.render(`mermaid-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, diagramText);

    // Parse SVG into a temp DOM element to read the viewBox dimensions
    const temp = document.createElement('div');
    temp.innerHTML = svg;
    const svgEl = temp.querySelector('svg');

    const viewBox = svgEl?.getAttribute('viewBox');
    // viewBox = "minX minY width height" — we need the width (3rd value)
    const parts = viewBox?.split(/\s+/);
    const svgWidth = parts && parts.length >= 3 ? parseFloat(parts[2]) : 800;

    // Keep the SVG at its natural viewBox size, but cap to the prose width.
    // height follows from the viewBox aspect ratio → no empty whitespace.
    svgEl?.setAttribute('width', `${Math.round(svgWidth)}px`);
    svgEl?.removeAttribute('height');
    svgEl?.setAttribute('style', 'max-width:100%;height:auto;display:block');

    return `<div class="mermaid-diagram flex justify-center">${temp.innerHTML}</div>`;
  } catch (e) {
    return `<pre class="text-red-500 bg-red-50 p-2 rounded">Mermaid Error: ${escapeHtml(e.message)}\n\n${escapeHtml(diagramText)}</pre>`;
  }
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
      renderMarkdown(textarea.value, previewDiv, { preview: true }).catch(() => {});
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
        renderMarkdown(textarea.value, previewDiv, { preview: true }).catch(() => {});
      }, 300);
    }
    if (options.onValueChange) {
      options.onValueChange(textarea.value);
    }
  });

  return { textarea, previewDiv, toggleBtn, isPreview: () => isPreview };
}