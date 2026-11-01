/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<object, object, unknown>
  export default component
}

declare module 'markdown-it' {
  import MarkdownIt from 'markdown-it'
  export default MarkdownIt
}

// highlight.js ships types for the default entry and for
// `highlight.js/lib/languages/*`, but not for the `lib/core` sub-path.
declare module 'highlight.js/lib/core' {
  import type { HLJSApi } from 'highlight.js'
  const hljs: HLJSApi
  export default hljs
}