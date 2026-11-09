import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import { copyFileSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

const spaRouteCopies = () => ({
  name: 'spa-route-copies',
  closeBundle() {
    const dist = resolve(__dirname, 'dist')
    for (const route of ['login', 'register', 'dashboard', 'assistant', 'documents', 'images', 'office', 'research', 'chat', 'video-studio', 'video-studio/editor', 'profile', 'admin']) {
      const target = resolve(dist, route, 'index.html')
      mkdirSync(resolve(dist, route), { recursive: true })
      copyFileSync(resolve(dist, 'index.html'), target)
    }
  },
})

export default defineConfig({
  plugins: [react(), spaRouteCopies(), VitePWA({
    registerType: 'autoUpdate',
    includeAssets: ['manifest.json'],
    manifest: {
      name: 'AI Workspace',
      short_name: 'AI Workspace',
      description: 'AI assistant workspace with voice chat and admin controls',
      start_url: './',
      display: 'standalone',
      theme_color: '#1f6654',
      background_color: '#eef3f0',
      icons: [
        { src: '/icon-192.svg', sizes: '192x192', type: 'image/svg+xml', purpose: 'any maskable' },
        { src: '/icon-512.svg', sizes: '512x512', type: 'image/svg+xml', purpose: 'any maskable' },
      ],
    },
  })],
  // The frontend is served from the site root. A relative base breaks asset
  // URLs on BrowserRouter deep links such as /chat and /video-studio/editor.
  base: '/'
})
