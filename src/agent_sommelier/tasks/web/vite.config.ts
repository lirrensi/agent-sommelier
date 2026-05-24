import { defineConfig } from 'vite'
import { resolve } from 'path'

const __dirname = resolve()

export default defineConfig({
  root: __dirname,
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
