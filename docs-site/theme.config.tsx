import React from 'react'
import { DocsThemeConfig } from 'nextra-theme-docs'

const config: DocsThemeConfig = {
  logo: (
    <span style={{ fontWeight: 800, fontSize: '1.1em' }}>
      syke<span style={{ color: '#6366f1' }}>.</span>
    </span>
  ),
  project: { link: 'https://github.com/saxenauts/syke' },
  docsRepositoryBase: 'https://github.com/saxenauts/syke/tree/main/docs-site',
  banner: {
    key: 'hackathon-2026',
    content: (
      <a href="https://syke-ai.vercel.app" target="_blank">
        Built at Claude Code Hackathon, Feb 2026. See the live demo →
      </a>
    ),
  },
  head: () => (
    <>
      <meta name="description" content="Syke — Cross-web working memory for AI. Ingests your digital footprint, synthesizes identity, distributes to every AI tool via MCP." />
      <meta property="og:title" content="Syke — Cross-Web Working Memory for AI" />
      <meta property="og:description" content="One daemon, continuous sync, every model knows you." />
    </>
  ),
  faviconGlyph: '🧠',
  color: { hue: 245, saturation: 60 },
  footer: {
    content: (
      <span>
        MIT {new Date().getFullYear()} ·{' '}
        <a href="https://github.com/saxenauts" target="_blank">Utkarsh Saxena</a> ·{' '}
        <a href="https://pypi.org/project/syke/" target="_blank">PyPI</a> ·{' '}
        <a href="https://syke-ai.vercel.app" target="_blank">Demo</a>
      </span>
    ),
  },
  toc: { backToTop: true },
  editLink: { content: 'Edit this page on GitHub →' },
  feedback: { content: null },
  darkMode: true,
}

export default config
